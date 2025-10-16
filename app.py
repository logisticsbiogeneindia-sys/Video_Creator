import streamlit as st
from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips
from PIL import Image
import numpy as np
import tempfile
import os

# Patch for Pillow >=10 compatibility with MoviePy
if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.LANCZOS

st.set_page_config(page_title="Images+Audio → MP4", layout="wide")

st.title("Create MP4 from Images + Audio")
st.write("Upload images (multiple) and one audio file — this app exports a single MP4 with the audio as soundtrack.")

with st.sidebar:
    st.header("Settings")
    duration_per_image = st.number_input("Default duration per image (seconds)", min_value=0.5, max_value=60.0, value=3.0, step=0.5)
    fps = st.number_input("FPS", min_value=1, max_value=60, value=24)
    width = st.number_input("Output width (px)", min_value=240, max_value=3840, value=1280)
    height = st.number_input("Output height (px)", min_value=240, max_value=3840, value=720)
    crossfade = st.checkbox("Enable crossfade between images", value=True)
    crossfade_duration = st.number_input("Crossfade duration (seconds)", min_value=0.0, max_value=5.0, value=0.7, step=0.1)
    outfile_name = st.text_input("Output filename", value="output.mp4")

st.header("Uploads")
images = st.file_uploader("Upload image files (jpg, png). Hold Ctrl / Cmd to multi-select.", type=["png","jpg","jpeg","webp","bmp"], accept_multiple_files=True)
audio = st.file_uploader("Upload one audio file (mp3, wav, m4a, ogg)", type=["mp3","wav","m4a","ogg"])

if not images or not audio:
    st.info("Please upload at least one image and one audio file to enable video creation.")

# Show thumbnails and simple ordering info
if images:
    st.subheader("Images preview (upload order)")
    cols = st.columns(5)
    for i, img_file in enumerate(images):
        try:
            img = Image.open(img_file)
            with cols[i % 5]:
                st.image(img, caption=f"{i}: {img_file.name}", use_column_width=True)
        except Exception as e:
            st.warning(f"Could not open {img_file.name}: {e}")

# Option to specify custom durations per image
durations = []
if images:
    st.subheader("Per-image durations (optional)")
    st.write("Leave empty to use the default duration set in the sidebar.")
    for i, img_file in enumerate(images):
        val = st.number_input(f"Duration for {img_file.name} (s)", min_value=0.1, max_value=600.0, value=float(duration_per_image), key=f"dur_{i}")
        durations.append(float(val))

# Button to create video
if st.button("Create MP4"):
    if not images or not audio:
        st.error("You must upload images and audio before creating a video.")
    else:
        try:
            with st.spinner("Rendering video — this may take a while depending on the files and settings..."):
                progress = st.progress(0)

                # Save the uploaded audio to a temp file
                tmp_dir = tempfile.mkdtemp()
                audio_path = os.path.join(tmp_dir, "audio" + os.path.splitext(audio.name)[1])
                with open(audio_path, "wb") as f:
                    f.write(audio.getbuffer())

                # Create ImageClips
                clips = []
                total = len(images)
                for idx, img_file in enumerate(images):
                    img = Image.open(img_file).convert("RGB")
                    img.thumbnail((width, height), Image.LANCZOS)
                    background = Image.new("RGB", (width, height), (0, 0, 0))
                    x = (width - img.width) // 2
                    y = (height - img.height) // 2
                    background.paste(img, (x, y))

                    # Save resized image to temp path
                    img_tmp_path = os.path.join(tmp_dir, f"resized_{idx}.png")
                    background.save(img_tmp_path, format="PNG")

                    dur = durations[idx] if durations else duration_per_image
                    clip = ImageClip(img_tmp_path).set_duration(dur)
                    clips.append(clip)

                    progress.progress(int((idx / (total + 1)) * 50))

                # Optionally crossfade
                if crossfade and crossfade_duration > 0 and len(clips) > 1:
                    final_clip = concatenate_videoclips(clips, method="compose", padding=-crossfade_duration)
                else:
                    final_clip = concatenate_videoclips(clips, method="compose")

                # Attach audio and write final file
                audio_clip = AudioFileClip(audio_path)
                final_clip = final_clip.set_audio(audio_clip)

                out_path = os.path.join(tmp_dir, outfile_name)
                final_clip.write_videofile(out_path, fps=int(fps), codec="libx264", audio_codec="aac", threads=4, verbose=False, logger=None)

                progress.progress(100)

            # Provide download
            with open(out_path, "rb") as f:
                video_bytes = f.read()
            st.success("Video created successfully!")
            st.video(video_bytes)
            st.download_button("Download MP4", data=video_bytes, file_name=outfile_name, mime="video/mp4")

        except Exception as e:
            st.exception(e)

st.markdown("---")
st.caption("Built with Streamlit + MoviePy. Works on Python 3.13 + Pillow ≥10.")