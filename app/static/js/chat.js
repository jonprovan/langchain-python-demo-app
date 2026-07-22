// Plain fetch() calls for the upload and chat pages -- no JS framework, kept
// deliberately simple since the teaching focus of this demo is the Python
// LangChain/LangGraph/Bedrock side, not the front end.

// --- Upload page: submit the file, then poll ingestion status ---
const uploadForm = document.getElementById("upload-form");
if (uploadForm) {
  uploadForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const statusEl = document.getElementById("upload-status");
    const fileInput = document.getElementById("file-input");

    const formData = new FormData();
    formData.append("file", fileInput.files[0]);

    statusEl.textContent = "Uploading...";
    const uploadResponse = await fetch("/documents/upload", {
      method: "POST",
      body: formData,
    });
    const uploadResult = await uploadResponse.json();

    if (!uploadResponse.ok) {
      statusEl.textContent = `Error: ${uploadResult.error}`;
      return;
    }

    statusEl.textContent = `Uploaded ${uploadResult.filename}. Ingesting...`;
    await pollIngestionStatus(uploadResult.ingestion_job_id, statusEl);
  });
}

// Poll the ingestion job status every 3 seconds until it leaves the
// "in progress" states, so the user knows when the doc is searchable.
async function pollIngestionStatus(jobId, statusEl) {
  while (true) {
    const response = await fetch(`/documents/status/${jobId}`);
    const result = await response.json();

    if (result.status === "COMPLETE") {
      statusEl.textContent = "Ingestion complete. You can now ask questions on the Chat page.";
      return;
    }
    if (result.status === "FAILED") {
      statusEl.textContent = "Ingestion failed. Check the AWS console for details.";
      return;
    }

    statusEl.textContent = `Ingestion status: ${result.status}...`;
    await new Promise((resolve) => setTimeout(resolve, 3000));
  }
}

// --- Chat page: submit the question, render the answer/citations/trace link ---
const chatForm = document.getElementById("chat-form");
if (chatForm) {
  chatForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const questionInput = document.getElementById("question-input");
    const resultEl = document.getElementById("chat-result");
    const answerEl = document.getElementById("answer-text");
    const citationsEl = document.getElementById("citations-list");
    const traceEl = document.getElementById("trace-link");

    answerEl.textContent = "Thinking...";
    resultEl.classList.remove("hidden");
    citationsEl.innerHTML = "";
    traceEl.textContent = "";

    const response = await fetch("/chat/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: questionInput.value }),
    });
    const result = await response.json();

    if (!response.ok) {
      answerEl.textContent = `Error: ${result.error}`;
      return;
    }

    answerEl.textContent = result.answer;

    result.citations.forEach((citation) => {
      const li = document.createElement("li");
      li.textContent = JSON.stringify(citation);
      citationsEl.appendChild(li);
    });

    if (result.trace_url) {
      const link = document.createElement("a");
      link.href = result.trace_url;
      link.target = "_blank";
      link.textContent = "View trace";
      traceEl.appendChild(link);
    }
  });
}
