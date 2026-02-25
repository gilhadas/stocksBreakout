"""Watchlist Manager — add, edit, delete watchlists."""
import streamlit as st

from utils import load_text, save_text, list_files, PROJECT_ROOT
INPUT_DIR = str(PROJECT_ROOT / 'input')


def _get_watchlists():
    """Return dict of {name: filename} for all watchlist .txt files."""
    names = list_files(INPUT_DIR, '*.txt')
    return {n.replace('.txt', ''): n
            for n in names if n != 'email_recipients.txt'}


def _read_watchlist(filename):
    """Read watchlist file and return raw content."""
    content = load_text(f"{INPUT_DIR}/{filename}")
    return content or ''


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
        content = uploaded.getvalue().decode('utf-8')
        save_text(content.strip(), f"{INPUT_DIR}/{uploaded.name}")
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
                fname = f"{clean_name}.txt"
                existing = set(list_files(INPUT_DIR, '*.txt'))
                if fname in existing:
                    st.error(f"**{clean_name}** already exists. Use edit below.")
                else:
                    save_text(new_symbols.strip(), f"{INPUT_DIR}/{fname}")
                    symbols = _parse_symbols(new_symbols)
                    st.success(
                        f"Created **{fname}** ({len(symbols)} symbols)"
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

    for name, filename in watchlists.items():
        content = _read_watchlist(filename)
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
                    save_text(edited.strip(), f"{INPUT_DIR}/{filename}")
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
                        import os
                        os.remove(f"{INPUT_DIR}/{filename}")
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
