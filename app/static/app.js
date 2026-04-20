(() => {
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/static/sw.js").catch(()=>{});
  }
  const hamb = document.getElementById("hamb");
  const dr = document.getElementById("drawer");
  const ov = document.getElementById("overlay");
  if (hamb && dr && ov) {
    const close = ()=>{ dr.classList.remove("open"); ov.classList.remove("show"); }
    hamb.addEventListener("click", ()=>{ dr.classList.add("open"); ov.classList.add("show"); });
    ov.addEventListener("click", close);
    document.addEventListener("keydown", (e)=>{ if(e.key==="Escape") close(); });
  }
  document.querySelectorAll(".feedback-form textarea").forEach((el) => {
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        const form = el.closest("form");
        if (form && el.value.trim()) {
          form.requestSubmit();
        }
      }
    });
  });
})();
