import streamlit as st
import os
import subprocess
import tempfile
from pathlib import Path

# ---------- Page Config ----------
st.set_page_config(
    page_title="Synctoon Video Generator",
    page_icon="🎬",
    layout="wide"
)

st.title("🎬 Synctoon – Script to 2D Animation")
st.caption("Apni script likho, character do, aur lip-sync animation banao.")

# ---------- API Keys from Streamlit Secrets ----------
try:
    ELEVEN_KEY = st.secrets["ELEVENLABS_API_KEY"]
    LLM_KEY = st.secrets.get("LLM_API_KEY", "")
    LLM_PROVIDER = st.secrets.get("LLM_PROVIDER", "anthropic")
except Exception:
    st.error("⚠️ Streamlit Secrets me API keys set karo (ELEVENLABS_API_KEY, LLM_API_KEY).")
    st.stop()

os.environ["ELEVENLABS_API_KEY"] = ELEVEN_KEY
os.environ["ANTHROPIC_API_KEY"] = LLM_KEY if LLM_PROVIDER == "anthropic" else ""
os.environ["OPENAI_API_KEY"] = LLM_KEY if LLM_PROVIDER == "openai" else ""

# ---------- Sidebar ----------
with st.sidebar:
    st.header("⚙️ Settings")
    duration = st.selectbox("Video duration", ["Short (1 min)", "Medium (3 min)", "Long (5+ min)"])
    style = st.selectbox("Animation style", ["Simple 2D", "Cartoon", "Stick figure"])
    seed_name = st.text_input("Output name", value="myvideo")
    st.info("Longer videos may time out on Streamlit free tier.")

# ---------- Main Inputs ----------
col1, col2 = st.columns(2)

with col1:
    st.subheader("📝 Script")
    script_text = st.text_area(
        "Apni script yahan likho:",
        height=300,
        placeholder="Once upon a time in a small warehouse..."
    )

with col2:
    st.subheader("🧑 Character")
    character_img = st.file_uploader(
        "Character image upload karo (PNG/JPG)",
        type=["png", "jpg", "jpeg"]
    )
    if character_img:
        st.image(character_img, caption="Your character", width=200)

# ---------- Generate Button ----------
if st.button("🚀 Generate Animation", type="primary"):
    if not script_text.strip():
        st.warning("Pehle script likho.")
        st.stop()

    with st.spinner("Animation ban rahi hai... (yeh kuch minute le sakta hai)"):
        try:
            # Temporary working directory
            workdir = tempfile.mkdtemp()
            script_path = Path(workdir) / "story.txt"
            script_path.write_text(script_text, encoding="utf-8")

            # Save character image if uploaded
            char_path = None
            if character_img:
                char_path = Path(workdir) / "character.png"
                char_path.write_bytes(character_img.read())

            # --- Call synctoon core ---
            # NOTE: synctoon ke actual CLI/entrypoint ke hisaab se
            # neeche wala command adjust karna padega.
            cmd = [
                "python", "core/create_animation.py",
                "--script", str(script_path),
                "-n", seed_name
            ]
            if char_path:
                cmd += ["--character", str(char_path)]

            result = subprocess.run(
                cmd,
                cwd="synctoon",          # repo root
                capture_output=True,
                text=True,
                timeout=1800             # 30 min max
            )

            st.text(result.stdout[-2000:])

            if result.returncode != 0:
                st.error("❌ Generation fail hui.")
                st.text(result.stderr[-2000:])
                st.stop()

            # --- Find output video ---
            video_path = Path("synctoon/videos") / f"{seed_name}.mp4"
            if not video_path.exists():
                st.error("Video file nahi mili. Path check karo.")
                st.stop()

            st.success("✅ Video ready!")
            st.video(str(video_path))

            with open(video_path, "rb") as f:
                st.download_button(
                    "⬇️ Download video",
                    f,
                    file_name=f"{seed_name}.mp4",
                    mime="video/mp4"
                )

        except subprocess.TimeoutExpired:
            st.error("⏱️ Timeout! Streamlit free tier par lambi video nahi banti. Chhoti script try karo.")
        except Exception as e:
            st.exception(e)
