// 1. Step 1 wala Direct Ad Link yahan paste karein
const AD_DIRECT_LINK = "https://your-ad-network-direct-link.com"; 

// 2. API Call Function (Jab user YouTube URL submit kare)
async function convertVideo(youtubeUrl) {
    const API_URL = "https://yt-audio-api-production-57a2.up.railway.app";
    
    try {
        const response = await fetch(`${API_URL}/?url=${encodeURIComponent(youtubeUrl)}`);
        const data = await response.json();

        if (data.download_url) {
            const downloadBtn = document.getElementById("downloadBtn");
            
            // Full Download Link save karein
            downloadBtn.setAttribute("data-download-url", `${API_URL}${data.download_url}`);
            downloadBtn.style.display = "block"; // Button display karein
        } else {
            alert("Conversion error: " + (data.error || "Failed"));
        }
    } catch (err) {
        console.error("Fetch error:", err);
    }
}

// 3. Download Button Click Event (Redirect + Download Logic)
document.getElementById("downloadBtn").addEventListener("click", function () {
    const fileUrl = this.getAttribute("data-download-url");
    if (!fileUrl) return;

    // A. Ad ko naye tab mein open karein
    window.open(AD_DIRECT_LINK, '_blank');

    // B. Direct File Download Start Karein
    const a = document.createElement('a');
    a.href = fileUrl;
    a.setAttribute('download', 'audio.mp3');
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
});
