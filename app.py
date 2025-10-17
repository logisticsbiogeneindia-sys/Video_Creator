import streamlit as st
from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips
from PIL import Image
import tempfile, os, math

# ---------------- PAGE CONFIG ----------------
st.set_page_config(page_title="🎵 Soft Light Video Creator", layout="wide", page_icon="🎬")

# ---------------- SOFT LIGHT THEME ----------------
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Nunito:wght@400;600;700&display=swap');
    
    .stApp { background: linear-gradient(135deg, #e0eafc 0%, #cfdef3 100%); font-family: 'Nunito', sans-serif; color: #1e1e2f; }
    .navbar { position: fixed; top: 0; left: 0; width: 100%; background: linear-gradient(90deg, #89f7fe 0%, #66a6ff 100%); color: #1e1e2f; font-weight: 700; padding: 1.1rem 2rem; font-size: 1.4rem; text-align: center; box-shadow: 0 4px 10px rgba(0,0,0,0.2); z-index: 999; }
    .block-container { padding-top: 100px; }
    h1,h2,h3 { color: #2a2a3d !important; text-shadow: 0px 0px 6px rgba(255,255,255,0.3); }
    div[data-testid="stFileUploader"] section { background: rgba(255,255,255,0.6); border-radius: 12px; border: 2px dashed #6a5acd; padding: 1rem; box-shadow: 0 4px 10px rgba(0,0,0,0.05); }
    div.stButton > button { background: linear-gradient(90deg, #4facfe 0%, #00f2fe 100%); color: #fff; border: none; border-radius: 10px; padding: 0.6rem 1.4rem; font-weight: 700; font-size: 1.05rem; transition: all 0.3s ease; }
    div.stButton > button:hover { transform: scale(1.05); background: linear-gradient(90deg, #00f2fe 0%, #4facfe 100%); box-shadow: 0 0 10px rgba(79,172,254,0.6); }
    section[data-testid="stSidebar"] { background: rgba(255,255,255,0.75); border-right: 2px solid rgba(102,166,255,0.3); backdrop-filter: blur(8px); }
    section[data-testid="stSidebar"] * { color: #1e1e2f !important; font-weight: 500; }
    .stAlert { background: rgba(255,255,255,0.8) !important; border-radius: 10px; border: 1px solid #a4b0be !important; color: #1e1e2f !important; font-weight: 600; text-align: center; }
    footer, .stCaption { text-align: center; color: #555; margin-top: 2rem; }
    </style>

    <div class="navbar">🎬 <b>Soft Light Video Creator</b> — Turn Audio + Images into Art 🌤</div>
""", unsafe_allow_html=True)

# ---------------- HEADER ----------------
st.title("✨ Create a Soft Cinematic Video with Audio + Images")
st.write("Upload images and an audio track — the images will loop until the song finishes 🎶")

# ---------------- SIDEBAR ----------------
with st.sidebar:
    st.header("⚙️ Settings")
    fps = st.slider("Frames per second (FPS)", 10, 60, 24)
    width = st.number_input("Width (px)", 480, 3840, 1280)
    height = st.number_input("Height (px)", 240, 2160, 720)
    outfile_name = st.text_input("Output filename", "softlight_video.mp4")

# ---------------- UPLOAD ----------------
st.subheader("📁 Upload Files")
audio_file = st.file_uploader("🎵 Upload audio file", type=["mp3","wav","m4a","ogg"])
images = st.file_uploader("🖼 Upload images", type=["png","jpg","jpeg","webp"], accept_multiple_files=True)

if not audio_file or not images:
    st.warning("⚠️ Please upload at least one image and one audio file to continue 🎬")

if audio_file:
    st.info(f"✅ Audio uploaded: {audio_file.name}")
if images:
    st.success(f"✅ {len(images)} image(s) uploaded successfully!")

# ---------------- CREATE VIDEO ----------------
if audio_file and images:
    if st.button("🚀 Create Video"):
        try:
            tmpdir = tempfile.mkdtemp()

            # Save audio
            audio_path = os.path.join(tmpdir, audio_file.name)
            with open(audio_path, "wb") as f:
                f.write(audio_file.read())
            audio_clip = AudioFileClip(audio_path)
            total_duration = audio_clip.duration
            num_images = len(images)

            # Save images
            img_paths = []
            for i, img_file in enumerate(images):
                img_path = os.path.join(tmpdir, f"img_{i}.png")
                with open(img_path, "wb") as f:
                    f.write(img_file.read())
                img_paths.append(img_path)

            # Image loop setup
            base_duration = 2.5  # seconds per image
            total_image_cycle = num_images * base_duration
            loop_count = math.ceil(total_duration / total_image_cycle)
            st.info(f"🔁 Looping {loop_count} times to match song length")

            # Progress bar
            total_clips = loop_count * num_images
            progress_bar = st.progress(0)
            progress_text = st.empty()

            clips = []
            count = 0
            for _ in range(loop_count):
                for img_path in img_paths:
                    clip = ImageClip(img_path).set_duration(base_duration).resize((width, height))
                    clips.append(clip)
                    count += 1
                    progress = int((count / total_clips) * 100)
                    progress_bar.progress(progress)
                    progress_text.text(f"Rendering video... {progress}%")

            # Concatenate & attach audio
            final_clip = concatenate_videoclips(clips).set_audio(audio_clip).subclip(0, total_duration)

            # Export
            output_path = os.path.join(tmpdir, outfile_name)
            final_clip.write_videofile(output_path, fps=fps, codec="libx264", audio_codec="aac", verbose=False, logger=None)

            progress_bar.progress(100)
            progress_text.text("Rendering complete! 🎉")
            st.success("🎉 Video created successfully!")
            st.video(output_path)
            with open(output_path, "rb") as f:
                st.download_button("📥 Download MP4", f, file_name=outfile_name, mime="video/mp4")

        except Exception as e:
            st.error(f"❌ Error: {e}")

st.markdown("<hr><center>🌈 Made with ❤️ by Mohit Sharma | Soft Light Theme</center>", unsafe_allow_html=True)
