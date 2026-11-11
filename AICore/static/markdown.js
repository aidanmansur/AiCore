/*
 * Kiçik və təhlükəsiz Markdown renderer.
 * Bütün mətn əvvəlcə HTML-escape olunur, sonra yalnız bizim yaratdığımız
 * teqlər əlavə edilir. Beləliklə model cavabı skript yeridə bilməz (XSS yoxdur).
 */
(function () {
    function esc(s) {
        return s
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

    function inline(s) {
        const codes = [];
        s = s.replace(/`([^`]+)`/g, function (_, c) {
            codes.push(c);
            return "\u0000" + (codes.length - 1) + "\u0000";
        });
        s = s.replace(
            /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
            '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>'
        );
        s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
        s = s.replace(/(^|[^*\w])\*([^*\s][^*]*?)\*(?!\*)/g, "$1<em>$2</em>");
        s = s.replace(/\u0000(\d+)\u0000/g, function (_, i) {
            return "<code>" + codes[Number(i)] + "</code>";
        });
        return s;
    }

    function splitRow(row) {
        return row.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(function (c) {
            return c.trim();
        });
    }

    const RE_BLOCK = /^\u0001(\d+)\u0001$/;
    const RE_HEADING = /^(#{1,6})\s+(.*)$/;
    const RE_HR = /^\s*(-{3,}|\*{3,})\s*$/;
    const RE_QUOTE = /^&gt;\s?(.*)$/;
    const RE_UL = /^\s*[-*•]\s+(.*)$/;
    const RE_OL = /^\s*\d+[.)]\s+(.*)$/;
    const RE_TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

    function isSpecial(line, next) {
        return (
            RE_BLOCK.test(line) || RE_HEADING.test(line) || RE_HR.test(line) ||
            RE_QUOTE.test(line) || RE_UL.test(line) || RE_OL.test(line) ||
            (line.indexOf("|") !== -1 && next !== undefined && RE_TABLE_SEP.test(next))
        );
    }

    function renderMarkdown(text) {
        const blocks = [];
        function stash(code) {
            blocks.push("<pre><code>" + esc(code.replace(/\n$/, "")) + "</code></pre>");
            return "\u0001" + (blocks.length - 1) + "\u0001";
        }

        // tam bağlanmış kod blokları, sonra axın zamanı hələ bağlanmamış blok
        text = text.replace(/```[^\n]*\n([\s\S]*?)```/g, function (_, code) { return "\n" + stash(code) + "\n"; });
        text = text.replace(/```[^\n]*\n([\s\S]*)$/, function (_, code) { return "\n" + stash(code) + "\n"; });

        const lines = esc(text).split("\n");
        const out = [];
        let i = 0;

        while (i < lines.length) {
            const line = lines[i];
            let m;

            if (!line.trim()) { i++; continue; }

            if ((m = line.match(RE_BLOCK))) { out.push(blocks[Number(m[1])]); i++; continue; }

            if ((m = line.match(RE_HEADING))) {
                const tag = m[1].length <= 2 ? "h3" : "h4";
                out.push("<" + tag + ">" + inline(m[2]) + "</" + tag + ">");
                i++; continue;
            }

            if (RE_HR.test(line)) { out.push("<hr>"); i++; continue; }

            if (RE_QUOTE.test(line)) {
                const items = [];
                while (i < lines.length && (m = lines[i].match(RE_QUOTE))) { items.push(inline(m[1])); i++; }
                out.push("<blockquote>" + items.join("<br>") + "</blockquote>");
                continue;
            }

            if (RE_UL.test(line)) {
                const items = [];
                while (i < lines.length && (m = lines[i].match(RE_UL))) { items.push("<li>" + inline(m[1]) + "</li>"); i++; }
                out.push("<ul>" + items.join("") + "</ul>");
                continue;
            }

            if (RE_OL.test(line)) {
                const items = [];
                while (i < lines.length && (m = lines[i].match(RE_OL))) { items.push("<li>" + inline(m[1]) + "</li>"); i++; }
                out.push("<ol>" + items.join("") + "</ol>");
                continue;
            }

            if (line.indexOf("|") !== -1 && i + 1 < lines.length && RE_TABLE_SEP.test(lines[i + 1])) {
                const head = splitRow(line);
                i += 2;
                const rows = [];
                while (i < lines.length && lines[i].indexOf("|") !== -1 && lines[i].trim()) {
                    rows.push(splitRow(lines[i]));
                    i++;
                }
                let html = "<div class=\"table-wrap\"><table><thead><tr>";
                head.forEach(function (c) { html += "<th>" + inline(c) + "</th>"; });
                html += "</tr></thead><tbody>";
                rows.forEach(function (r) {
                    html += "<tr>";
                    r.forEach(function (c) { html += "<td>" + inline(c) + "</td>"; });
                    html += "</tr>";
                });
                html += "</tbody></table></div>";
                out.push(html);
                continue;
            }

            // adi paraqraf
            const para = [];
            while (i < lines.length && lines[i].trim() && !(para.length && isSpecial(lines[i], lines[i + 1]))) {
                para.push(inline(lines[i]));
                i++;
            }
            out.push("<p>" + para.join("<br>") + "</p>");
        }

        return out.join("");
    }

    window.renderMarkdown = renderMarkdown;
})();
