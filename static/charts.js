(() => {
    const palette = ["#138a65", "#438bd1", "#e7a737", "#df7275", "#8d78cd", "#45a5ad", "#d874a2", "#718178", "#74aa62"];
    const chartInstances = [];
    const readJSON = (id) => {
        const element = document.getElementById(id);
        if (!element) return [];
        try {
            return JSON.parse(element.textContent);
        } catch (_error) {
            return [];
        }
    };

    function themeColors() {
        const styles = getComputedStyle(document.documentElement);
        return {
            text: styles.getPropertyValue("--muted").trim(),
            grid: styles.getPropertyValue("--line").trim(),
            currency: document.getElementById("expenseChart")?.dataset.currency || "INR"
        };
    }

    function createCharts() {
        if (!window.Chart) return;
        const labels = readJSON("chart-labels");
        const values = readJSON("chart-values");
        const months = readJSON("month-labels");
        const monthlyValues = readJSON("monthly-values");
        const doughnut = document.getElementById("expenseChart");
        const trend = document.getElementById("lineChart");
        const colors = themeColors();
        window.Chart.defaults.font.family = '"DM Sans", sans-serif';
        window.Chart.defaults.color = colors.text;

        if (doughnut && labels.length) {
            chartInstances.push(new window.Chart(doughnut, {
                type: "doughnut",
                data: {
                    labels,
                    datasets: [{
                        data: values,
                        backgroundColor: labels.map((_, index) => palette[index % palette.length]),
                        borderColor: getComputedStyle(document.documentElement).getPropertyValue("--surface").trim(),
                        borderWidth: 3,
                        hoverOffset: 6
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: "68%",
                    plugins: {
                        legend: {
                            position: "right",
                            labels: { usePointStyle: true, pointStyle: "circle", boxWidth: 8, padding: 14, color: colors.text }
                        },
                        tooltip: {
                            callbacks: {
                                label(context) {
                                    return ` ${context.label}: ${colors.currency} ${Number(context.raw).toFixed(2)}`;
                                }
                            }
                        }
                    }
                }
            }));
        }

        if (trend && months.length) {
            const context = trend.getContext("2d");
            const gradient = context.createLinearGradient(0, 0, 0, 280);
            gradient.addColorStop(0, "rgba(19, 138, 101, .25)");
            gradient.addColorStop(1, "rgba(19, 138, 101, 0)");
            chartInstances.push(new window.Chart(trend, {
                type: "line",
                data: {
                    labels: months,
                    datasets: [{
                        label: "Monthly spending",
                        data: monthlyValues,
                        borderColor: "#138a65",
                        backgroundColor: gradient,
                        fill: true,
                        tension: .38,
                        borderWidth: 2.5,
                        pointRadius: 3,
                        pointHoverRadius: 5,
                        pointBackgroundColor: "#138a65"
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: { intersect: false, mode: "index" },
                    plugins: {
                        legend: { display: false },
                        tooltip: { callbacks: { label: (context) => ` ${colors.currency} ${Number(context.raw).toFixed(2)}` } }
                    },
                    scales: {
                        x: { grid: { display: false }, ticks: { color: colors.text } },
                        y: {
                            beginAtZero: true,
                            grid: { color: colors.grid },
                            ticks: {
                                color: colors.text,
                                callback: (value) => `${colors.currency} ${value}`
                            }
                        }
                    }
                }
            }));
        }

        window.addEventListener("ledger:themechange", () => {
            const current = themeColors();
            window.Chart.defaults.color = current.text;
            chartInstances.forEach((chart) => {
                chart.options.plugins.tooltip.titleColor = current.text;
                chart.options.plugins.tooltip.bodyColor = current.text;
                if (chart.options.plugins.legend?.labels) chart.options.plugins.legend.labels.color = current.text;
                if (chart.options.scales) {
                    Object.values(chart.options.scales).forEach((scale) => {
                        scale.ticks.color = current.text;
                        if (scale.grid) scale.grid.color = current.grid;
                    });
                }
                const dataset = chart.data.datasets[0];
                if (chart.config.type === "doughnut") dataset.borderColor = getComputedStyle(document.documentElement).getPropertyValue("--surface").trim();
                chart.update();
            });
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", createCharts);
    } else {
        createCharts();
    }
})();
