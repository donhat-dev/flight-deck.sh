/** The markdown subset a Claude Code transcript actually contains.
 *
 * Everything is escaped BEFORE any tag is emitted, so the output carries no
 * markup the source did not spell out and needs no sanitizer behind it. Link
 * targets are checked separately, because escaping an attribute value does not
 * make `javascript:` safe.
 */
import { markup } from "@odoo/owl";

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const FENCE = /^\s*(```|~~~)\s*([\w+-]*)\s*$/;
const HEADING = /^(#{1,6})\s+(.*)$/;
const BULLET = /^\s*[-*+]\s+(.*)$/;
const ORDERED = /^\s*\d+[.)]\s+(.*)$/;
const QUOTE = /^\s*>\s?(.*)$/;
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;
const TABLE_SEP = /^\s*\|?[\s:|-]+\|[\s:|-]*$/;

function esc(text) {
    return String(text ?? "").replace(/[&<>"']/g, (c) => ESCAPES[c]);
}

function safeUrl(raw) {
    const url = raw.trim();
    return /^(https?:\/\/|mailto:|#|\/)/i.test(url) ? esc(url) : null;
}

/** Inline spans, run on already-escaped text. Code is lifted out first, so
 * nothing inside a span of code is read as emphasis; the private-use markers
 * cannot collide with escaped text. */
function inline(escaped) {
    const codes = [];
    let out = escaped.replace(/`([^`]+)`/g, (_, code) => {
        codes.push(code);
        return "\uE000" + (codes.length - 1) + "\uE001";
    });
    out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (whole, label, href) => {
        const url = safeUrl(href);
        return url ? `<a href="${url}" target="_blank" rel="noreferrer">${label}</a>` : whole;
    });
    out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    out = out.replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s.,;:)!?]|$)/g, "$1<em>$2</em>");
    out = out.replace(/(^|[\s(])_([^_\n]+)_(?=[\s.,;:)!?]|$)/g, "$1<em>$2</em>");
    out = out.replace(/~~([^~]+)~~/g, "<del>$1</del>");
    return out.replace(/\uE000(\d+)\uE001/g, (_, i) => `<code>${codes[Number(i)]}</code>`);
}

function tableRow(line) {
    return line
        .trim()
        .replace(/^\||\|$/g, "")
        .split("|")
        .map((cell) => inline(esc(cell.trim())));
}

/** Markdown to HTML, as a value `t-out` can print. */
export function renderMarkdown(source) {
    const lines = String(source ?? "").split("\n");
    const out = [];
    let paragraph = [];
    let list = null;
    let quote = [];

    const flushParagraph = () => {
        if (paragraph.length) {
            out.push(`<p>${inline(esc(paragraph.join("\n"))).replace(/\n/g, "<br/>")}</p>`);
            paragraph = [];
        }
    };
    const flushList = () => {
        if (list) {
            out.push(`</${list}>`);
            list = null;
        }
    };
    const flushQuote = () => {
        if (quote.length) {
            out.push(
                `<blockquote>${inline(esc(quote.join("\n"))).replace(/\n/g, "<br/>")}</blockquote>`
            );
            quote = [];
        }
    };
    const flushAll = () => {
        flushParagraph();
        flushList();
        flushQuote();
    };

    for (let i = 0; i < lines.length; i++) {
        const line = lines[i];

        const fence = line.match(FENCE);
        if (fence) {
            flushAll();
            const body = [];
            const closer = fence[1];
            i++;
            while (i < lines.length && !lines[i].trim().startsWith(closer)) {
                body.push(lines[i]);
                i++;
            }
            const lang = fence[2] ? ` data-lang="${esc(fence[2])}"` : "";
            out.push(`<pre class="fd_md_code"${lang}><code>${esc(body.join("\n"))}</code></pre>`);
            continue;
        }

        if (!line.trim()) {
            flushAll();
            continue;
        }
        if (RULE.test(line)) {
            flushAll();
            out.push("<hr/>");
            continue;
        }

        const heading = line.match(HEADING);
        if (heading) {
            flushAll();
            const level = Math.min(heading[1].length + 2, 6);
            out.push(`<h${level}>${inline(esc(heading[2]))}</h${level}>`);
            continue;
        }

        // A table needs its separator row to be a table at all.
        if (line.includes("|") && TABLE_SEP.test(lines[i + 1] || "")) {
            flushAll();
            const head = tableRow(line);
            i += 2;
            const body = [];
            while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
                body.push(tableRow(lines[i]));
                i++;
            }
            i--;
            const th = head.map((c) => `<th>${c}</th>`).join("");
            const rows = body
                .map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`)
                .join("");
            out.push(
                `<table class="fd_md_table"><thead><tr>${th}</tr></thead><tbody>${rows}</tbody></table>`
            );
            continue;
        }

        const quoted = line.match(QUOTE);
        if (quoted) {
            flushParagraph();
            flushList();
            quote.push(quoted[1]);
            continue;
        }
        flushQuote();

        const bullet = line.match(BULLET);
        const ordered = line.match(ORDERED);
        if (bullet || ordered) {
            flushParagraph();
            const kind = bullet ? "ul" : "ol";
            if (list !== kind) {
                flushList();
                out.push(`<${kind}>`);
                list = kind;
            }
            out.push(`<li>${inline(esc((bullet || ordered)[1]))}</li>`);
            continue;
        }
        flushList();
        paragraph.push(line);
    }
    flushAll();
    return markup(out.join(""));
}
