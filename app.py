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

# ---------- Load Secrets ----------
try:
    ELEVEN_KEY = st.secrets["ELEVENLABS_API_KEY"]
    LLM_KEY = st.secrets["LLM_API_KEY"]
    LLM_PROVIDER = st.secrets.get("LLM_PROVIDER", "gemini").lower()
except Exception as e:
    st.error("⚠️ Streamlit Secrets me API keys set karo.")
    st.code("""
ELEVENLABS_API_KEY = "sk_..."
LLM_API_KEY = "your_key"
LLM_PROVIDER = "gemini"
    """, language="toml")
    st.stop()

# ---------- Set Env Vars ----------
os.environ["ELEVENLABS_API_KEY"] = ELEVEN_KEY
os.environ["LLM_PROVIDER"] = LLM_PROVIDER

if LLM_PROVIDER == "gemini":
    os.environ["GEMINI_API_KEY"] = LLM_KEY
    os.environ["GOOGLE_API_KEY"] = LLM_KEY
elif LLM_PROVIDER == "anthropic":
    os.environ["ANTHROPIC_API_KEY"] = LLM_KEY
elif LLM_PROVIDER == "openai":
    os.environ["OPENAI_API_KEY"] = LLM_KEY
else:
    st.error(f"❌ Unknown LLM_PROVIDER: {LLM_PROVIDER}. Use 'gemini', 'anthropic', or 'openai'.")
    st.stop()

# ---------- Sidebar ----------
with st.sidebar:
    st.header("⚙️ Settings")
    st.write(f"**Provider:** `{LLM_PROVIDER}`")
    
    duration = st.selectbox(
        "Video duration",
        ["Short (30 sec)", "Medium (1 min)", "Long (3 min)"]
    )
    style = st.selectbox(
        "Animation style",
        ["Simple 2D", "Cartoon", "Stick figure"]
    )
    seed_name = st.text_input("Output name", value="myvideo")
    
    st.warning("⚠️ Streamlit free tier par lambi video timeout ho sakti hai. 1-min se shuru karo.")

# ---------- Main Inputs ----------
col1, col2 = st.columns(2)

with col1:
    st.subheader("📝 Script")
    script_text = st.text_area(
        "Apni script yahan likho:",
        height=350,
        placeholder="Once upon a time in a small warehouse, a worker named Raj started his day..."
    )
    char_count = len(script_text)
    st.caption(f"Characters: {char_count} / 10,000 (ElevenLabs free limit)")

with col2:
    st.subheader("🧑 Character")
    character_img = st.file_uploader(
        "Character image upload karo (PNG/JPG)",
        type=["png", "jpg", "jpeg"]
    )
    if character_img:
        st.image(character_img, caption="Your character", width=200)
    else:
        st.info("Optional: character image upload karo consistency ke liye.")

# ---------- Generate Button ----------
if st.button("🚀 Generate Animation", type="primary", use_container_width=True):
    if not script_text.strip():
        st.warning("Pehle script likho.")
        st.stop()

    if char_count > 10000:
        st.warning("Script 10,000 characters se zyada hai. Chhoti karo, warna ElevenLabs fail hoga.")
        st.stop()

    progress = st.progress(0, text="Setup ho raha hai...")
    log_box = st.empty()

    try:
        # ---------- Temp workdir ----------
        workdir = tempfile.mkdtemp()
        script_path = Path(workdir) / "story.txt"
        script_path.write_text(script_text, encoding="utf-8")
        progress.progress(10, text="Script save ho gayi...")

        # ---------- Save character ----------
        char_path = None
        if character_img:
            char_path = Path(workdir) / "character.png"
            char_path.write_bytes(character_img.read())
        progress.progress(20, text="Character ready...")

        # ---------- Build command ----------
        cmd = [
            "python", "core/create_animation.py",
            "--script", str(script_path),
            "-n", seed_name
        ]
        if char_path:
            cmd += ["--character", str(char_path)]

        log_box.code(" ".join(cmd), language="bash")
        progress.progress(30, text="Animation ban rahi hai... (5-15 min lag sakte hain)")

        # ---------- Run synctoon ----------
        result = subprocess.run(
            cmd,
            cwd="synctoon",
            capture_output=True,
            text=True,
            timeout=1500  # 25 min
        )

        progress.progress(80, text="Render complete, video dhundh rahe hain...")

        # ---------- Show logs ----------
        if result.stdout:
            with st.expander("📜 stdout"):
                st.text(result.stdout[-3000:])
        if result.stderr:
            with st.expander("⚠️ stderr"):
                st.text(result.stderr[-3000:])

        if result.returncode != 0:
            st.error("❌ Generation fail hui. Upar wale logs dekho.")
            st.stop()

        # ---------- Find output video ----------
        possible_paths = [
            Path("synctoon/videos") / f"{seed_name}.mp4",
            Path("synctoon/videos") / f"{seed_name}_final.mp4",
            Path("synctoon/output") / f"{seed_name}.mp4",
        ]
        video_path = next((p for p in possible_paths if p.exists()), None)

        if not video_path:
            st.error("Video file nahi mili. Synctoon ka output path check karo.")
            st.write("Searched paths:")
            for p in possible_paths:
                st.code(str(p))
            st.stop()

        progress.progress(100, text="✅ Done!")

        # ---------- Show video ----------
        st.success("🎉 Video ready!")
        st.video(str(video_path))

        with open(video_path, "rb") as f:
            st.download_button(
                "⬇️ Download Video",
                f,
                file_name=f"{seed_name}.mp4",
                mime="video/mp4",
                use_container_width=True
            )

    except subprocess.TimeoutExpired:
        st.error("⏱️ Timeout! Streamlit free tier 25 min ke baad kill kar deta hai. Chhoti script try karo.")
    except FileNotFoundError as e:
        st.error(f"❌ File nahi mili: {e}. Check karo synctoon folder sahi jagah hai.")
    except Exception as e:
        st.exception(e)
