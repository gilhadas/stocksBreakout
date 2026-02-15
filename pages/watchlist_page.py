"""Watchlist Manager — add, edit, delete watchlists."""
import streamlit as st
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
INPUT_DIR = PROJECT_ROOT / 'input'


def _get_watchlists():
    """Return dict of {name: path} for all watchlist .txt files."""
    if not INPUT_DIR.exists():
        return {}
    files = sorted(INPUT_DIR.glob('*.txt'))
    return {f.stem: f for f in files if f.stem != 'email_recipients'}


def _read_watchlist(path):
    """Read watchlist file and return raw content."""
    return path.read_text().strip()


def _parse_symbols(content):
    """Parse symbols from watchlist content (same logic as utils.py)."""
    symbols = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith('###'):
            continue
        for s in line.split(','):
            s = s.strip()
            if s and not s.startswith('###'):
                clean = s.split(':')[-1]
                if clean:
                    symbols.append(clean)
    return symbols


def render_watchlist_page():
    st.header("Watchlist Manager")

    watchlists = _get_watchlists()

    # ── Upload section ──
    st.subheader("Upload Watchlist")
    uploaded = st.file_uploader(
        "Upload a .txt file with comma-separated symbols",
        type=['txt'],
        key="wl_page_upload",
    )
    if uploaded:
        INPUT_DIR.mkdir(exist_ok=True)
        dest = INPUT_DIR / uploaded.name
        content = uploaded.getvalue().decode('utf-8')
        dest.write_text(content)
        symbols = _parse_symbols(content)
        st.success(f"Saved **{uploaded.name}** ({len(symbols)} symbols)")
        # Refresh list
        watchlists = _get_watchlists()

    st.markdown("---")

    # ── Create new watchlist ──
    st.subheader("Create New Watchlist")
    col_name, col_btn = st.columns([3, 1])
    with col_name:
        new_name = st.text_input(
            "Name", placeholder="my_watchlist", key="new_wl_name"
        )
    new_symbols = st.text_area(
        "Symbols (comma-separated)",
        placeholder="AAPL, MSFT, GOOGL, TSLA",
        key="new_wl_symbols",
        height=80,
    )
    with col_btn:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("Create", type="primary", use_container_width=True):
            if new_name and new_symbols.strip():
                clean_name = new_name.strip().replace(' ', '_')
                dest = INPUT_DIR / f"{clean_name}.txt"
                if dest.exists():
                    st.error(f"**{clean_name}** already exists. Use edit below.")
                else:
                    INPUT_DIR.mkdir(exist_ok=True)
                    dest.write_text(new_symbols.strip())
                    symbols = _parse_symbols(new_symbols)
                    st.success(
                        f"Created **{clean_name}.txt** ({len(symbols)} symbols)"
                    )
                    watchlists = _get_watchlists()
            else:
                st.warning("Enter a name and at least one symbol.")

    st.markdown("---")

    # ── Existing watchlists ──
    st.subheader(f"Watchlists ({len(watchlists)})")

    if not watchlists:
        st.info("No watchlists found. Upload or create one above.")
        return

    for name, path in watchlists.items():
        content = _read_watchlist(path)
        symbols = _parse_symbols(content)

        with st.expander(f"**{name}** — {len(symbols)} symbols"):
            # Symbol badges
            badge_html = " ".join(
                f'<span style="background:#262730; border:1px solid #444; '
                f'border-radius:4px; padding:2px 8px; margin:2px; '
                f'font-size:12px; display:inline-block;">{s}</span>'
                for s in symbols
            )
            st.markdown(badge_html, unsafe_allow_html=True)

            st.markdown("")

            # Editable content
            edited = st.text_area(
                "Edit symbols",
                value=content,
                key=f"edit_{name}",
                height=120,
            )

            col_save, col_del, col_spacer = st.columns([1, 1, 3])

            with col_save:
                if st.button("Save", key=f"save_{name}", use_container_width=True):
                    path.write_text(edited.strip())
                    new_symbols_list = _parse_symbols(edited)
                    st.success(f"Saved ({len(new_symbols_list)} symbols)")

            with col_del:
                if st.button(
                    "Delete", key=f"del_{name}", use_container_width=True
                ):
                    st.session_state[f'confirm_del_{name}'] = True

            # Delete confirmation
            if st.session_state.get(f'confirm_del_{name}'):
                st.warning(f"Delete **{name}.txt**? This cannot be undone.")
                c1, c2, _ = st.columns([1, 1, 3])
                with c1:
                    if st.button(
                        "Yes, delete",
                        key=f"confirm_yes_{name}",
                        type="primary",
                        use_container_width=True,
                    ):
                        path.unlink()
                        st.session_state.pop(f'confirm_del_{name}', None)
                        st.success(f"Deleted {name}.txt")
                        st.rerun()
                with c2:
                    if st.button(
                        "Cancel",
                        key=f"confirm_no_{name}",
                        use_container_width=True,
                    ):
                        st.session_state.pop(f'confirm_del_{name}', None)
                        st.rerun()
