"""Shared Virginia Tech banner for Streamlit apps (portal.datasci.vt.edu).

Renders a thin maroon VT banner fixed to the top of every page, with the
Virginia Tech horizontal logo on the left and a single "Home" link.

The logo is embedded below as a base64 PNG so this file is fully self-contained
and survives the flat rsync to /shared/apps with no external asset dependency.

Usage (call once, immediately after st.set_page_config):

    from vt_banner import render_vt_banner
    render_vt_banner()
"""

import streamlit as st

VT_MAROON = "#861F41"
VT_ORANGE = "#E87722"
HOME_URL = "http://portal.datasci.vt.edu"

_LOGO_B64 = (
    "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"
    "UmcAAAAASUVORK5CYII="
)

_BANNER_HTML = """
<style>
  /* Streamlit's own header: make it see-through and click-through so the
     maroon banner underneath shows and its Home link stays clickable... */
  [data-testid="stHeader"] {{ background: transparent; pointer-events: none; }}
  /* ...but keep Streamlit's top-right menu/toolbar usable, above the banner. */
  [data-testid="stToolbar"] {{ pointer-events: auto; z-index: 1000001; }}

  #vt-banner {{
      position: fixed; top: 0; left: 0; right: 0; height: 46px;
      background: {maroon}; display: flex; align-items: center; gap: 22px;
      padding: 0 14px 0 22px; z-index: 1000000;
      box-shadow: 0 1px 4px rgba(0, 0, 0, 0.25);
  }}
  #vt-banner img {{ height: 26px; }}
  #vt-banner a.vt-home {{
      color: #ffffff; text-decoration: none; font-weight: 600;
      font-size: 0.95rem;
      font-family: "Source Sans Pro", -apple-system, BlinkMacSystemFont, sans-serif;
  }}
  #vt-banner a.vt-home:hover {{ color: {orange}; }}
  /* push the app body (and sidebar) down so nothing hides under the banner */
  .block-container {{ padding-top: 3.5rem; }}
  section[data-testid="stSidebar"] > div {{ padding-top: 2.5rem; }}
</style>
<div id="vt-banner">
  <img src="data:image/png;base64,{logo}" alt="Virginia Tech">
  <a class="vt-home" href="{home}" target="_self">Home</a>
</div>
"""


def render_vt_banner(home_url: str = HOME_URL) -> None:
    """Render the maroon VT banner at the top of the current page."""
    st.markdown(
        _BANNER_HTML.format(
            maroon=VT_MAROON, orange=VT_ORANGE, logo=_LOGO_B64, home=home_url
        ),
        unsafe_allow_html=True,
    )
