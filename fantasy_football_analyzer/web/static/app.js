/* Fantasy Football Analyzer - shared UI helpers */

/* ------------------------------------------------------------------ */
/* Chart.js dark-theme defaults (charts read colors from CSS tokens)   */
/* ------------------------------------------------------------------ */

if (typeof Chart !== "undefined") {
    const styles = getComputedStyle(document.documentElement);
    Chart.defaults.color = "#adb5bd";
    Chart.defaults.borderColor = "rgba(255, 255, 255, 0.08)";
    Chart.defaults.font.family =
        'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';
    Chart.defaults.plugins.tooltip.backgroundColor = "rgba(20, 24, 28, 0.95)";
    Chart.defaults.plugins.tooltip.borderColor = "rgba(255, 255, 255, 0.15)";
    Chart.defaults.plugins.tooltip.borderWidth = 1;
    Chart.defaults.plugins.tooltip.padding = 10;
    Chart.defaults.plugins.legend.labels.boxWidth = 12;
    Chart.defaults.plugins.legend.labels.boxHeight = 12;

    window.FFA_CHART_COLORS = {
        blue: styles.getPropertyValue("--ffa-chart-blue").trim() || "#3987e5",
        gold: styles.getPropertyValue("--ffa-chart-gold").trim() || "#c98500",
        muted: styles.getPropertyValue("--ffa-chart-muted").trim() || "#5c6670",
        green: styles.getPropertyValue("--ffa-field").trim() || "#2e9e5b",
    };
}

/* ------------------------------------------------------------------ */
/* Sortable tables: <table class="table-sortable"> with data-sort ths  */
/* ------------------------------------------------------------------ */

document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("table.table-sortable").forEach((table) => {
        table.querySelectorAll("thead th[data-sort]").forEach((th, ) => {
            th.addEventListener("click", () => sortTable(table, th));
        });
    });
});

function sortTable(table, th) {
    const headers = Array.from(th.parentNode.children);
    const colIndex = headers.indexOf(th);
    const type = th.dataset.sort; // "num" or "text"
    const current = th.getAttribute("aria-sort");
    const ascending = current === "descending"; // default first click: descending

    headers.forEach((h) => h.removeAttribute("aria-sort"));
    th.setAttribute("aria-sort", ascending ? "ascending" : "descending");

    const tbody = table.querySelector("tbody");
    const rows = Array.from(tbody.querySelectorAll("tr"));

    rows.sort((a, b) => {
        const av = cellValue(a, colIndex, type);
        const bv = cellValue(b, colIndex, type);
        if (av < bv) return ascending ? -1 : 1;
        if (av > bv) return ascending ? 1 : -1;
        return 0;
    });

    rows.forEach((row) => tbody.appendChild(row));
}

function cellValue(row, index, type) {
    const cell = row.children[index];
    if (!cell) return type === "num" ? -Infinity : "";
    const text = cell.textContent.trim();
    if (type === "num") {
        const num = parseFloat(text.replace(/[^0-9.+-]/g, ""));
        return Number.isNaN(num) ? -Infinity : num;
    }
    return text.toLowerCase();
}
