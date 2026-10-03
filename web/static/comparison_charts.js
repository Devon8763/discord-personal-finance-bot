/* Comparison-page presentation only. Statistics and exact labels come from the core. */
(() => {
  "use strict";
  // Progressive enhancement: without this script, all generated dates remain readable.
  const toggle = document.getElementById("comparison-all-dates");
  if (toggle) {
    const rows = [...document.querySelectorAll("#comparison-daily-table [data-daily-row]")];
    const empty = document.getElementById("comparison-daily-empty");
    const filterRows = () => {
      rows.forEach(row => { row.hidden = !toggle.checked && row.dataset.nonzero !== "true"; });
      empty.hidden = rows.some(row => !row.hidden);
    };
    document.getElementById("comparison-daily-filter").hidden = false;
    toggle.checked = false;
    toggle.addEventListener("change", filterRows);
    filterRows();
  }
  const source = document.getElementById("comparison-chart-data");
  const status = document.getElementById("comparison-chart-status");
  if (!source || !status || typeof Chart === "undefined") return;
  const charts = [];
  try {
    const data = JSON.parse(source.textContent);
    const colors = data.categories.map((_, i) => `hsl(${(i * 137.508) % 360}, 65%, 43%)`);
    const periodColors = ["#2563eb", "#c2410c"];
    const started = data.periods.map((period, index) => ({ period, index })).filter(p => p.period.started);
    const coordinate = cents => cents === null ? null : Number(cents) / 100;
    const amountTick = value => `${Number(value).toLocaleString("zh-TW", { maximumFractionDigits: 2 })} 元`;
    const legend = { onClick: () => {}, onHover: () => {} };
    function draw(id, type, chartData, options) {
      const canvas = document.getElementById(id);
      if (!canvas) return;
      canvas.parentElement.hidden = false;
      const chart = new Chart(canvas, {
        type, data: chartData,
        options: {
          responsive: true, maintainAspectRatio: false, animation: false,
          ...options,
          plugins: { legend, ...options.plugins }
        }
      });
      charts.push(chart);
      if (!chart.ctx) throw new Error("Chart canvas unavailable");
    }

    const longest = started.reduce((points, p) => p.period.daily.length > points.length ? p.period.daily : points, []);
    draw("comparison-line", "line", {
      labels: longest.map(p => `第 ${p.day} 天`),
      datasets: started.map(({ period, index }) => ({
        label: period.label + (index === 0 ? "（實線）" : "（虛線）"),
        data: period.daily.map(p => coordinate(p.cents)),
        period, borderColor: periodColors[index], backgroundColor: periodColors[index],
        borderDash: index === 0 ? [] : [7, 4], pointStyle: index === 0 ? "circle" : "triangle",
        pointRadius: period.daily.length === 1 ? 5 : 2,
        pointHitRadius: 8,
        tension: 0, spanGaps: false, fill: false
      }))
    }, {
      scales: {
        x: { title: { display: true, text: "相對日序" } },
        y: { beginAtZero: true, title: { display: true, text: "每日支出（元）" }, ticks: { callback: amountTick } }
      },
      plugins: { tooltip: { callbacks: {
        title: () => "",
        label: context => {
          const point = context.dataset.period.daily[context.dataIndex];
          return `${context.dataset.period.label}｜第 ${point.day} 天｜${point.date}｜當日 ${point.amount} 元`;
        }
      } } }
    });

    const categoryCanvas = document.getElementById("comparison-category-chart");
    if (categoryCanvas) categoryCanvas.parentElement.style.height = `${Math.max(320, data.categories.length * 58 + 80)}px`;
    draw("comparison-category-chart", "bar", {
      labels: data.categories.map(row => row.name),
      datasets: started.map(({ period, index }) => ({
        label: period.label + (index === 0 ? "（實色）" : "（淡色與外框）"),
        periodIndex: index,
        data: data.categories.map(row => coordinate(row[index === 0 ? "a_cents" : "b_cents"])),
        backgroundColor: colors.map(color => index === 0 ? color : color.replace(")", ", 0.35)").replace("hsl(", "hsla(")),
        borderColor: colors, borderWidth: index === 0 ? 0 : 2
      }))
    }, {
      indexAxis: "y",
      scales: { x: { beginAtZero: true, title: { display: true, text: "分類支出（元）" }, ticks: { callback: amountTick } },
        y: { ticks: { autoSkip: false } } },
      plugins: { tooltip: { callbacks: {
        label: context => {
          const key = context.dataset.periodIndex === 0 ? "a" : "b";
          return `${data.periods[context.dataset.periodIndex].label}：${data.categories[context.dataIndex][key + "_amount"]} 元`;
        }
      } } }
    });

    if (!data.single_category) data.periods.forEach((period, index) => {
      const key = index === 0 ? "a" : "b";
      // Only omit zero wedges; the complete category table remains untouched.
      const slices = period.share_order.map(i => ({ row: data.categories[i], color: colors[i] }));
      draw("comparison-pie-" + key, "pie", {
        labels: slices.map(slice => slice.row.name),
        datasets: [{ data: slices.map(slice => coordinate(slice.row[key + "_cents"])),
          backgroundColor: slices.map(slice => slice.color) }]
      }, {
        plugins: { legend, tooltip: { callbacks: {
          label: context => {
            const row = slices[context.dataIndex].row;
            return `${period.label}｜${row.name}：${row[key + "_amount"]} 元（${row[key + "_percent"]}%）`;
          }
        } } }
      });
    });
    status.hidden = true;
  } catch {
    charts.forEach(chart => chart.destroy());
    document.querySelectorAll(".comparison-chart-box").forEach(box => { box.hidden = true; });
    status.textContent = "圖表未載入，請使用文字摘要、分類表與每日支出數據。";
  }
})();
