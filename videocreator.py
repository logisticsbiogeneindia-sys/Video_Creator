import os
import shutil
import requests
from gtts import gTTS
from moviepy.editor import *
from transformers import pipeline
from PIL import Image
import streamlit as st
import tempfile
import time

# ================= CONFIG =================
PEXELS_API_KEY = st.secrets["pexels"]["api_key"]
NUM_IMAGES = 6
VIDEO_SIZE = (1280, 720)
# ==========================================

# ---------- Functions ----------
def generate_script(topic):
    """Generate motivational script using HuggingFace GPT2"""
    generator = pipeline("text-generation", model="gpt2")
    prompt = f"Write a powerful 2-minute motivational speech about {topic}, inspiring and positive, suitable for YouTube."
    result = generator(prompt, max_length=500, do_sample=True, top_p=0.95, temperature=0.8)
    return result[0]["generated_text"]

def generate_voiceover(text, filename):
    """Convert text to speech using gTTS"""
    tts = gTTS(text=text, lang="en")
    tts.save(filename)
    return filename

def fetch_images(query, count, folder):
    """Download images from Pexels API to temp folder"""
    headers = {"Authorization": PEXELS_API_KEY}
    url = f"https://api.pexels.com/v1/search?query={query}&per_page={count}"
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise Exception(f"Pexels API error: {response.status_code} {response.text}")

    data = response.json()
    if "photos" not in data or len(data["photos"]) == 0:
        raise Exception(f"No images found for query: {query}")

    image_files = []
    for i, photo in enumerate(data["photos"]):
        img_url = photo["src"]["large"]
        img_data = requests.get(img_url).content
        filename = os.path.join(folder, f"image_{i}.jpg")
        with open(filename, "wb") as f:
            f.write(img_data)
        image_files.append(filename)
    return image_files

def create_video(voice_file, images, output, music_file=None):
    """Assemble video from images and voiceover"""
    clips = []
    audio = AudioFileClip(voice_file)
    duration = max(audio.duration / len(images), 2)

    for img_path in images:
        img = Image.open(img_path).convert("RGB")
        img = img.resize(VIDEO_SIZE, Image.Resampling.LANCZOS)
        temp_path = img_path.replace(".jpg", "_resized.jpg")
        img.save(temp_path)
        clip = ImageClip(temp_path).set_duration(duration)
        clips.append(clip)

    video = concatenate_videoclips(clips, method="compose").set_audio(audio)

    if music_file:
        music = AudioFileClip(music_file).volumex(0.2).subclip(0, audio.duration)
        video = video.set_audio(CompositeAudioClip([video.audio, music]))

    video.write_videofile(output, fps=24, codec="libx264", audio_codec="aac")

    # Clean up resized images
    for img_path in images:
        temp_path = img_path.replace(".jpg", "_resized.jpg")
        if os.path.exists(temp_path):
            os.remove(temp_path)

# ---------- Streamlit App ----------
st.title("🎬 AI Motivational Video Generator")

# User Inputs
topic = st.text_input("Enter the topic for the motivational video:")
music_file = st.file_uploader("Optional: Upload background music (MP3)", type=["mp3"])
output_file_name = st.text_input("Output video filename (e.g., final_video.mp4):", value="final_video.mp4")

if st.button("Generate Video"):
    if not topic.strip():
        st.error("Please enter a topic!")
    elif not output_file_name.strip():
        st.error("Please enter an output filename!")
    else:
        progress = st.progress(0)
        temp_dir = tempfile.mkdtemp()
        output_file = os.path.join(temp_dir, output_file_name)

        try:
            # Step 1: Generate script
            st.info("📝 Generating script...")
            script = generate_script(topic)
            st.text_area("Generated Script:", script, height=200)
            progress.progress(20)
            time.sleep(0.5)

            # Step 2: Generate voiceover
            st.info("🎤 Generating voiceover...")
            voice_path = os.path.join(temp_dir, "voice.mp3")
            generate_voiceover(script, voice_path)
            progress.progress(40)
            time.sleep(0.5)

            # Step 3: Fetch images
            st.info("📸 Downloading images...")
            images = fetch_images(topic, NUM_IMAGES, temp_dir)
            progress.progress(60)
            time.sleep(0.5)

            # Step 4: Handle optional music
            music_path = None
            if music_file:
                music_path = os.path.join(temp_dir, music_file.name)
                with open(music_path, "wb") as f:
                    f.write(music_file.getbuffer())

            # Step 5: Create video
            st.info("🎬 Creating video...")
            create_video(voice_path, images, output_file, music_path)
            progress.progress(100)

            st.success("✅ Video created successfully!")
            st.video(output_file)

        finally:
            # Cleanup temporary folder
            shutil.rmtree(temp_dir, ignore_errors=True)
