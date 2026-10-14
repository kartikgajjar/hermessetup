/**
 * src/mcp/helpers.ts — the high-level tools composed SERVER-SIDE from executor
 * primitives. They never touch the wire directly: `extract_links` and
 * `read_as_markdown` read via primitives; `fill_form` batches its field writes
 * when the backend can (else sequences fill+click).
 * (Only `download_file` is privileged and lives on the executor.)
 */

import { ExecutorError } from '../executor/types';
import type { Executor, FrameOpts, Target } from '../executor/types';
import { htmlToMarkdown } from './markdown-extract';

export interface LinkOut {
  href: string;
  text: string;
  ref?: string;
}

/**
 * Collect anchors from the page (or a subtree). Implemented as a single page
 * eval so it is one round-trip; falls back to parsing getHtml if eval is denied.
 *
 * `dedupe` collapses anchors that share an href (nav/footer repetition is common
 * noise when crawling); `limit` caps the number of links returned. Both are
 * applied server-side after collection, so they work on either code path.
 */
export async function extractLinks(
  ex: Executor,
  args: { selector?: string; sameOriginOnly?: boolean; dedupe?: boolean; limit?: number; tabId?: string } & FrameOpts,
): Promise<{ links: LinkOut[] }> {
  const root = args.selector ? JSON.stringify(args.selector) : 'null';
  const expr = `(() => {
    const root = ${root} ? document.querySelector(${root}) : document;
    if (!root) return [];
    const here = location.origin;
    return [...root.querySelectorAll('a[href]')].map(a => ({
      href: a.href, text: (a.textContent || '').trim().slice(0, 200),
    })).filter(l => l.href && (${args.sameOriginOnly ? 'l.href.startsWith(here)' : 'true'}));
  })()`;

  const frames: FrameOpts = { frameId: args.frameId, allFrames: args.allFrames };
  let links: LinkOut[];
  // eval is OFF by default (safe-mode) and a denial is THROWN as POLICY_DENIED, not
  // returned as `{ ok: false }`, so the fallback below has to be reached by catching
  // it. (Local fix, see VENDORED.md: before this, extract_links failed outright in the
  // default configuration and this fallback was dead code.) Only an eval denial falls
  // back; any other error still propagates.
  let res: Awaited<ReturnType<Executor['eval']>> | null = null;
  try {
    res = await ex.eval(expr, { tabId: args.tabId, ...frames });
  } catch (err) {
    if (!(err instanceof ExecutorError && err.code === 'POLICY_DENIED')) throw err;
  }
  if (res && res.ok && Array.isArray(res.value)) {
    links = res.value as LinkOut[];
  } else {
    // Fallback: parse hrefs out of the HTML. The DOM's `a.href` is absolute but the
    // serialized attribute is not, so resolve against the tab's URL and decode
    // attribute entities (`&amp;` is everywhere in real hrefs); then apply the same
    // sameOriginOnly filter the eval path applies.
    const { html } = await ex.getHtml(args.selector ? { selector: args.selector } : undefined, {
      tabId: args.tabId,
      ...frames,
    });
    const base = await pageUrl(ex, args.tabId);
    const origin = base ? new URL(base).origin : null;
    links = [];
    const re = /<a[^>]*href=["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi;
    let m: RegExpExecArray | null;
    while ((m = re.exec(html)) !== null) {
      const href = resolveHref(decodeEntities(m[1]), base);
      if (!href) continue;
      if (args.sameOriginOnly && origin && !href.startsWith(origin)) continue;
      links.push({ href, text: decodeEntities(m[2].replace(/<[^>]+>/g, '')).trim().slice(0, 200) });
    }
  }
  return { links: refineLinks(links, args) };
}

/** The URL of `tabId` (or the active tab), or null when it cannot be determined. */
async function pageUrl(ex: Executor, tabId?: string): Promise<string | null> {
  try {
    const tabs = await ex.tabsList();
    const tab = tabId ? tabs.find((t) => t.tabId === tabId) : tabs.find((t) => t.active);
    return tab?.url || null;
  } catch {
    return null;
  }
}

function decodeEntities(text: string): string {
  return text
    .replace(/&amp;/g, '&')
    .replace(/&quot;/g, '"')
    .replace(/&#0*39;|&apos;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>');
}

/** Resolve a serialized href against the page URL; null for fragments and non-navigable schemes. */
function resolveHref(href: string, base: string | null): string | null {
  const h = href.trim();
  if (!h || h.startsWith('#') || /^(javascript|data|blob):/i.test(h)) return null;
  if (/^[a-z][a-z0-9+.-]*:/i.test(h)) return h; // already absolute: leave as written
  try {
    return new URL(h, base ?? undefined).href;
  } catch {
    return null; // relative href with no usable base
  }
}

/**
 * Collapse links that share an href (keeping the first, but preferring a
 * non-empty label) when `dedupe` is set, then cap to `limit`. Order is
 * preserved so the first occurrence of each href wins.
 */
function refineLinks(links: LinkOut[], opts: { dedupe?: boolean; limit?: number }): LinkOut[] {
  let out = links;
  if (opts.dedupe) {
    const byHref = new Map<string, LinkOut>();
    for (const l of links) {
      const existing = byHref.get(l.href);
      if (!existing) byHref.set(l.href, { ...l });
      else if (!existing.text && l.text) existing.text = l.text;
    }
    out = [...byHref.values()];
  }
  if (opts.limit !== undefined && out.length > opts.limit) out = out.slice(0, opts.limit);
  return out;
}

/** Read a page (or subtree) as readable markdown. */
export async function readAsMarkdown(
  ex: Executor,
  args: { selector?: string; tabId?: string } & FrameOpts,
): Promise<string> {
  const { html } = await ex.getHtml(args.selector ? { selector: args.selector } : undefined, {
    tabId: args.tabId,
    frameId: args.frameId,
    allFrames: args.allFrames,
  });
  return htmlToMarkdown(html);
}

/**
 * Fill a set of fields (keyed by selector) and optionally submit. All writes go
 * out as ONE executor call when the backend can batch them; otherwise (older
 * extension, CDP) they fall back to one fill/click per field. Either way the
 * first failing field throws, and submit is a separate click after the fills.
 */
export async function fillForm(
  ex: Executor,
  args: { fields: Record<string, string | boolean>; submitSelector?: string; tabId?: string } & FrameOpts,
): Promise<{ filled: number; submitted: boolean }> {
  const opts = { tabId: args.tabId, frameId: args.frameId, allFrames: args.allFrames };
  const ops = Object.entries(args.fields).map(([selector, value]) => ({ selector, value }));
  const batched = ops.length > 0 && ex.fillFields ? await ex.fillFields(ops, opts) : null;
  let filled = batched?.filled ?? 0;
  if (!batched) {
    for (const { selector, value } of ops) {
      const target: Target = { selector };
      if (typeof value === 'boolean') {
        // Checkbox/radio: a click toggles it.
        await ex.click(target, opts);
      } else {
        await ex.fill(target, value, opts);
      }
      filled++;
    }
  }
  let submitted = false;
  if (args.submitSelector) {
    await ex.click({ selector: args.submitSelector }, opts);
    submitted = true;
  }
  return { filled, submitted };
}
