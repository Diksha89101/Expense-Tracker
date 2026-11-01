(() => {
    const root = document.documentElement;

    function setTheme(theme) {
        root.dataset.theme = theme;
        try {
            localStorage.setItem("theme", theme);
        } catch (_error) {
            // The theme still works for this page when storage is unavailable.
        }
        window.dispatchEvent(new CustomEvent("ledger:themechange", { detail: { theme } }));
    }

    function initializeTheme() {
        const toggle = document.querySelector(".theme-toggle");
        if (!toggle) return;
        toggle.addEventListener("click", () => {
            setTheme(root.dataset.theme === "dark" ? "light" : "dark");
        });
        toggle.setAttribute("aria-pressed", String(root.dataset.theme === "dark"));
        window.addEventListener("ledger:themechange", (event) => {
            toggle.setAttribute("aria-pressed", String(event.detail.theme === "dark"));
        });
    }

    function initializeToasts() {
        document.querySelectorAll(".toast").forEach((toast) => {
            const dismiss = () => {
                toast.classList.add("toast-leaving");
                window.setTimeout(() => toast.remove(), 180);
            };
            toast.querySelector(".toast-close")?.addEventListener("click", dismiss);
            window.setTimeout(dismiss, 5000);
        });
    }

    function initializeForms() {
        document.querySelectorAll("form").forEach((form) => {
            form.addEventListener("submit", (event) => {
                const start = form.querySelector('[name="start_date"]');
                const end = form.querySelector('[name="end_date"]');
                if (start?.value && end?.value && start.value > end.value) {
                    event.preventDefault();
                    end.setCustomValidity("End date must be on or after the start date.");
                    end.reportValidity();
                    return;
                }
                end?.setCustomValidity("");
                if (!form.checkValidity()) {
                    event.preventDefault();
                    form.reportValidity();
                    return;
                }
                const submit = event.submitter || form.querySelector('button[type="submit"], input[type="submit"]');
                if (!submit || form.method.toLowerCase() === "get") return;
                submit.disabled = true;
                submit.dataset.originalText = submit.textContent.trim();
                submit.classList.add("is-loading");
                submit.setAttribute("aria-busy", "true");
                submit.textContent = "Please wait…";
            });
        });
    }

    function initializeDeleteDialog() {
        const dialog = document.getElementById("delete-dialog");
        const form = document.getElementById("delete-form");
        if (!dialog || !form) return;
        const name = document.getElementById("delete-expense-name");
        document.querySelectorAll("[data-delete-url]").forEach((button) => {
            button.addEventListener("click", () => {
                form.action = button.dataset.deleteUrl;
                name.textContent = button.dataset.deleteTitle || "this expense";
                dialog.showModal();
            });
        });
        dialog.querySelector("[data-dialog-cancel]")?.addEventListener("click", () => dialog.close());
        dialog.addEventListener("click", (event) => {
            if (event.target === dialog) dialog.close();
        });
    }

    function initializeDateDefaults() {
        const today = new Date();
        const localDate = new Date(today.getTime() - today.getTimezoneOffset() * 60000)
            .toISOString()
            .slice(0, 10);
        document.querySelectorAll('input[type="date"][data-default-today]').forEach((input) => {
            if (!input.value) input.value = localDate;
        });
    }

    document.addEventListener("DOMContentLoaded", () => {
        document.querySelectorAll("[data-go-back]").forEach((button) => {
            button.addEventListener("click", () => {
                if (window.history.length > 1) window.history.back();
                else window.location.assign("/");
            });
        });
        initializeTheme();
        initializeToasts();
        initializeForms();
        initializeDeleteDialog();
        initializeDateDefaults();
    });
})();
