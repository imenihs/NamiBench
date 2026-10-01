// 認証の画面（設計A §4.3.2 の 2）。文言はサーバーが画面に入れたもの（words）と、失敗の応答の error.text だけ。
// QR か URL で開いたとき: アドレスの #k=<鍵> を読んでサーバーへ送り、すぐアドレスから消す。
// 番号で入るとき: 6桁の認証番号を送る。どちらも通ればサーバーがクッキーを渡すので、画面を読み直す。
(function () {
  "use strict";

  const words = JSON.parse(document.getElementById("words").textContent || "{}");
  const msg = document.getElementById("msg");
  const form = document.getElementById("auth");
  const input = document.getElementById("code");

  function keyFromHash() {
    const m = /(?:^#|&)k=([^&]+)/.exec(location.hash || "");
    return m ? decodeURIComponent(m[1]) : null;
  }
  function clearHash() {
    // 画面を人に見せても鍵が見えないよう、アドレスから消す（履歴にも残さない）
    history.replaceState(null, "", location.pathname + location.search);
  }

  async function send(body) {
    msg.textContent = words.checking || "";
    let res;
    try {
      res = await fetch("/api/v1/auth", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        credentials: "same-origin",
      });
    } catch (e) {
      msg.textContent = String(e);
      return false;
    }
    if (res.ok) { location.reload(); return true; }
    let text = "";
    try { const b = await res.json(); text = (b.error && (b.error.text || b.error.code)) || ""; } catch (e) { text = String(res.status); }
    msg.textContent = text;
    return false;
  }

  const key = keyFromHash();
  if (key) {
    clearHash();
    send({ key });
  }
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const code = input.value.trim();
    if (code) send({ code }).then((ok) => { if (!ok) { input.select(); } });
  });
})();
