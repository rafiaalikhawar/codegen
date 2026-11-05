// Progressive enhancement: mobile nav, view toggle, confirm dialog, inline form validation, toasts.
document.addEventListener("DOMContentLoaded", function () {
  var toggle = document.querySelector(".nav-toggle");
  if (toggle) toggle.addEventListener("click", function () {
    var open = document.querySelector(".nav-links").classList.toggle("open");
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
  });

  var view = document.querySelector(".view");
  if (view) {
    var key = "view:" + view.dataset.entity, saved = null;
    try { saved = localStorage.getItem(key); } catch (e) {}
    var set = function (v) {
      view.dataset.view = v;
      document.querySelectorAll(".view-toggle button").forEach(function (b) { b.classList.toggle("on", b.dataset.view === v); });
      try { localStorage.setItem(key, v); } catch (e) {}
    };
    set(saved === "table" ? "table" : "grid");
    document.querySelectorAll(".view-toggle button").forEach(function (b) { b.addEventListener("click", function () { set(b.dataset.view); }); });
  }

  var dialog = document.getElementById("confirm");
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (form.dataset.confirmed === "1") return;
      event.preventDefault();
      if (!dialog || !dialog.showModal) { if (window.confirm(form.dataset.confirm)) { form.dataset.confirmed = "1"; form.submit(); } return; }
      document.getElementById("confirm-text").textContent = form.dataset.confirm;
      dialog.returnValue = "";
      dialog.addEventListener("close", function handler() {
        dialog.removeEventListener("close", handler);
        if (dialog.returnValue === "ok") { form.dataset.confirmed = "1"; form.submit(); }
      });
      dialog.showModal();
    });
  });

  document.querySelectorAll("form.record-form").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      var first = null;
      form.querySelectorAll("input, select, textarea").forEach(function (field) {
        var group = field.closest(".form-group");
        if (!group) return;
        var old = group.querySelector(".field-error.client");
        if (old) old.remove();
        group.classList.remove("has-error");
        if (!field.checkValidity()) {
          var p = document.createElement("p");
          p.className = "field-error client"; p.setAttribute("role", "alert"); p.textContent = field.validationMessage;
          group.appendChild(p); group.classList.add("has-error"); first = first || field;
        }
      });
      if (first) { event.preventDefault(); first.focus(); }
    });
  });

  document.querySelectorAll(".toast").forEach(function (t) { setTimeout(function () { t.remove(); }, 4600); });
});
