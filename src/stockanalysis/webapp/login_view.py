"""
login_view.py — the sign-in page
================================
A standalone page, not a `render_page()` body: the shared layout carries the
sidebar, the global ticker search and the job-polling loop, all of which hit
endpoints a signed-out visitor has no business reaching. Rendering the login
form outside that layout means a logged-out browser fetches exactly one thing.

Colors and control styling are lifted from views._STYLE so the page reads as
part of the same tool rather than a bolted-on gate.
"""

from __future__ import annotations

from stockanalysis.webapp.views import esc

_LOGIN_STYLE = """
* { box-sizing:border-box }
body { margin:0; min-height:100vh; display:flex; align-items:center;
       justify-content:center; padding:24px;
       font-family:-apple-system,"Segoe UI",Roboto,sans-serif;
       background:#faf9f5; color:#0b0b0b }
.loginwrap { width:100%; max-width:380px }
.brand { display:flex; align-items:center; gap:9px; justify-content:center;
         font-weight:700; font-size:17px; margin-bottom:4px }
.tagline { text-align:center; font-size:12px; color:#898781; margin-bottom:22px }
.card { background:white; border:0.5px solid #e1e0d9; border-radius:12px;
        padding:24px 22px }
label { display:block; font-size:10px; color:#898781; text-transform:uppercase;
        letter-spacing:.3px; margin-bottom:5px }
input { width:100%; font-size:13px; padding:9px 11px; border:1px solid #d9d7ce;
        border-radius:6px; font-family:inherit; background:white }
input:focus { outline:none; border-color:#185FA5; box-shadow:0 0 0 3px #E6F1FB }
.field { margin-bottom:14px }
.btn { width:100%; font-family:inherit; font-size:13px; font-weight:600;
       padding:10px 14px; border:none; border-radius:7px; background:#185FA5;
       color:white; cursor:pointer; margin-top:4px }
.btn:hover { background:#0C447C }
.error { background:#FCEBEB; color:#791F1F; font-size:12px; padding:10px 12px;
         border-radius:8px; margin-bottom:16px }
.note { background:#E6F1FB; color:#0C447C; font-size:12px; padding:10px 12px;
        border-radius:8px; margin-bottom:16px }
.foot { text-align:center; font-size:11px; color:#898781; margin-top:16px;
        line-height:1.5 }
"""


def render_login(error: str = "", username: str = "", next_path: str = "/",
                 notice: str = "") -> bytes:
    """Full HTML for the sign-in page.

    `error` and `notice` are already-decided user-facing strings; everything
    interpolated here goes through esc() because `username` and `next_path`
    come straight off the request.
    """
    err_html = f'<div class="error">{esc(error)}</div>' if error else ""
    note_html = f'<div class="note">{esc(notice)}</div>' if notice else ""
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in · Trading Workstation</title>
<style>{_LOGIN_STYLE}</style></head><body>
<div class="loginwrap">
  <div class="brand"><span>🖥</span><span>Trading Workstation</span></div>
  <div class="tagline">Sign in to continue</div>
  <div class="card">
    {err_html}{note_html}
    <form method="POST" action="/login" autocomplete="on">
      <input type="hidden" name="next" value="{esc(next_path)}">
      <div class="field">
        <label for="username">Username</label>
        <input id="username" name="username" value="{esc(username)}"
               autocomplete="username" autofocus required>
      </div>
      <div class="field">
        <label for="password">Password</label>
        <input id="password" name="password" type="password"
               autocomplete="current-password" required>
      </div>
      <button class="btn" type="submit">Sign in</button>
    </form>
  </div>
  <div class="foot">Local tool · bound to 127.0.0.1<br>
    Forgot the password? Run
    <code>python app.py --set-password</code></div>
</div>
</body></html>""".encode()
