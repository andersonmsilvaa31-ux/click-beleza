const privacyForm = document.getElementById("privacy-form");
if (privacyForm) {
  const feedback = document.getElementById("privacy-feedback");
  privacyForm.addEventListener("submit", async event => {
    event.preventDefault();
    const button = privacyForm.querySelector("button[type=submit]");
    button.disabled = true;
    feedback.textContent = "Enviando…";
    try {
      const result = await chamarAPI("privacy/requests", {
        method: "POST",
        body: JSON.stringify(Object.fromEntries(new FormData(privacyForm)))
      });
      feedback.textContent = result.message;
      privacyForm.reset();
    } catch (error) {
      feedback.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
}
