(() => {
    "use strict";

    document.querySelectorAll("[data-password-toggle]").forEach((button) => {
        const field = document.getElementById(button.getAttribute("aria-controls"));
        if (!field) return;
        const isConfirmation = field.name === "confirm_password";
        button.addEventListener("click", () => {
            const show = field.type === "password";
            field.type = show ? "text" : "password";
            button.setAttribute("aria-pressed", String(show));
            button.setAttribute("aria-label", `${show ? "Ocultar" : "Mostrar"} ${isConfirmation ? "confirmação da senha" : "senha"}`);
            button.querySelector("[data-password-show]").hidden = show;
            button.querySelector("[data-password-hide]").hidden = !show;
        });
    });

    const registerForm = document.getElementById("registerForm");
    if (registerForm) {
        const password = registerForm.elements.namedItem("password");
        const confirmation = registerForm.elements.namedItem("confirm_password");
        const validateConfirmation = () => {
            confirmation.setCustomValidity(
                confirmation.value && confirmation.value !== password.value
                    ? "As senhas precisam ser iguais."
                    : ""
            );
        };
        password.addEventListener("input", validateConfirmation);
        confirmation.addEventListener("input", validateConfirmation);
    }

    document.querySelectorAll("[data-auth-form]").forEach((form) => {
        const button = form.querySelector("[data-submit-button]");
        const label = button?.querySelector("[data-submit-label]");
        if (!button || !label) return;
        const originalLabel = label.textContent;

        form.addEventListener("submit", () => {
            button.disabled = true;
            button.setAttribute("aria-busy", "true");
            label.textContent = form.id === "registerForm" ? "Criando sua conta..." : "Entrando...";
        });

        window.addEventListener("pageshow", () => {
            button.disabled = false;
            button.removeAttribute("aria-busy");
            label.textContent = originalLabel;
        });
    });
})();
