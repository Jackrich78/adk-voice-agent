/**
 * app.js: JS code for the adk-streaming sample app.
 */

/**
 * WebSocket handling
 */

// Global variables
const sessionId = Math.random().toString().substring(10);
const ws_url = "ws://" + window.location.host + "/ws/" + sessionId;
let websocket = null;
let is_audio = false;
let currentMessageId = null; // Track the current message ID during a conversation turn

// Get DOM elements
const messageForm = document.getElementById("messageForm");
const messageInput = document.getElementById("message");
const messagesDiv = document.getElementById("messages");
const statusDot = document.getElementById("status-dot");
const connectionStatus = document.getElementById("connection-status");
const typingIndicator = document.getElementById("typing-indicator");
const startAudioButton = document.getElementById("startAudioButton");
const stopAudioButton = document.getElementById("stopAudioButton");
const recordingContainer = document.getElementById("recording-container");

// WebSocket handlers
function connectWebsocket() {
  // Connect websocket
  const wsUrl = ws_url + "?is_audio=" + is_audio;
  websocket = new WebSocket(wsUrl);

  // Handle connection open
  websocket.onopen = function () {
    // Connection opened messages
    console.log("WebSocket connection opened.");
    connectionStatus.textContent = "Connected";
    statusDot.classList.add("connected");

    // Enable the Send button
    document.getElementById("sendButton").disabled = false;
    addSubmitHandler();
  };

  // Handle incoming messages
  websocket.onmessage = function (event) {
    const message_from_server = JSON.parse(event.data);
    console.log("[AGENT TO CLIENT RAW MSG] ", JSON.stringify(message_from_server)); // Log the raw data

    if (message_from_server.mime_type === "application/json" && message_from_server.type === "status") {
        console.log("[CLIENT STATUS UPDATE]: ", message_from_server.data);
        // If it's "processing_completed_audio", we might want to keep typing indicator visible
        if (message_from_server.data === "audio_received_processing") {
            typingIndicator.classList.add("visible");
        }
        return; 
    }

    if (message_from_server.turn_complete === true) {
      currentMessageId = null; // Reset for the next turn from the agent
      typingIndicator.classList.remove("visible");
      console.log("[TURN COMPLETE RECEIVED FROM SERVER]");
      return;
    }
    // If it's an interruption, also clear typing
    if (message_from_server.interrupted === true) {
        typingIndicator.classList.remove("visible");
        currentMessageId = null;
        console.log("[AGENT INTERRUPTED]");
        // return; // Decide if you want to stop processing further parts on interruption
    }


    // If it's audio, play it (Your existing logic for this is fine)
    if (message_from_server.mime_type === "audio/pcm" && audioPlayerNode) {
      typingIndicator.classList.remove("visible"); // Audio is playing, not typing
      audioPlayerNode.port.postMessage(base64ToArray(message_from_server.data));
      if (currentMessageId) {
        const messageElem = document.getElementById(currentMessageId);
        if (messageElem && !messageElem.querySelector(".audio-icon") && messagesDiv.classList.contains("audio-enabled")) {
          const audioIcon = document.createElement("span");
          audioIcon.className = "audio-icon";
          messageElem.prepend(audioIcon);
        }
      }
    }

    // Handle text messages (Corrected Logic)
    if (message_from_server.mime_type === "text/plain") {
      typingIndicator.classList.remove("visible"); // We are receiving text, so agent is not "typing" in the traditional sense.
                                                    // Typing indicator should ideally be shown by client *before* server response.

      const role = message_from_server.role || "model";
      const isMsgPartial = message_from_server.partial === true;

      let messageElem;
      if (role === "model") {
        if (currentMessageId) { // If we have an active bubble for this model's turn
          messageElem = document.getElementById(currentMessageId);
          if (messageElem) {
            if (isMsgPartial) {
              // Subsequent part of a stream, append
              messageElem.appendChild(document.createTextNode(message_from_server.data));
            } else {
              // Final part of a stream (or a complete non-streamed message)
              // Replace the content of the existing bubble with this final complete text.
              let iconNode = messageElem.querySelector('.audio-icon');
              messageElem.innerHTML = ''; // Clear previous partial content
              if (iconNode) messageElem.appendChild(iconNode); // Preserve icon
              messageElem.appendChild(document.createTextNode(message_from_server.data));
              currentMessageId = null; // This model's turn/bubble is now complete and finalized.
            }
          } else {
             currentMessageId = null; // Fallback: existing element not found, treat as new.
          }
        }
        
        // If messageElem is still not defined, it means this is the FIRST part of a model's response for this turn.
        if (!messageElem) {
          const messageId = Math.random().toString(36).substring(7);
          messageElem = document.createElement("p");
          messageElem.id = messageId;
          messageElem.className = "agent-message";

          if (messagesDiv.classList.contains("audio-enabled")) {
            const audioIcon = document.createElement("span");
            audioIcon.className = "audio-icon";
            messageElem.appendChild(audioIcon);
          }
          messageElem.appendChild(document.createTextNode(message_from_server.data));
          messagesDiv.appendChild(messageElem);

          if (isMsgPartial) {
            currentMessageId = messageId; // This is the first part of a new stream, save its ID
          } else {
            currentMessageId = null; // This was a complete message, no more partials needed for this ID.
          }
        }
      } else { // User message (role !== "model")
        const messageId = Math.random().toString(36).substring(7);
        messageElem = document.createElement("p");
        messageElem.id = messageId;
        messageElem.className = "user-message";
        messageElem.appendChild(document.createTextNode(message_from_server.data));
        messagesDiv.appendChild(messageElem);
        currentMessageId = null; // User messages don't use currentMessageId for streaming from server
      }
      messagesDiv.scrollTop = messagesDiv.scrollHeight;
    }
  };

  // Handle connection close
  websocket.onclose = function () {
    console.log("WebSocket connection closed.");
    document.getElementById("sendButton").disabled = true;
    connectionStatus.textContent = "Disconnected. Reconnecting...";
    statusDot.classList.remove("connected");
    typingIndicator.classList.remove("visible");
    setTimeout(function () {
      console.log("Reconnecting...");
      connectWebsocket();
    }, 5000);
  };

  websocket.onerror = function (e) {
    console.log("WebSocket error: ", e);
    connectionStatus.textContent = "Connection error";
    statusDot.classList.remove("connected");
    typingIndicator.classList.remove("visible");
  };
}
connectWebsocket();

// Add submit handler to the form
function addSubmitHandler() {
  messageForm.onsubmit = function (e) {
    e.preventDefault();
    const message = messageInput.value;
    if (message) {
      const p = document.createElement("p");
      p.textContent = message;
      p.className = "user-message";
      messagesDiv.appendChild(p);
      messageInput.value = "";

      // Show typing indicator after sending message
      typingIndicator.classList.add("visible");

      sendMessage({
        mime_type: "text/plain",
        data: message,
        role: "user",
      });
      console.log("[CLIENT TO AGENT] " + message);
      // Scroll down to the bottom of the messagesDiv
      messagesDiv.scrollTop = messagesDiv.scrollHeight;
    }
    return false;
  };
}

// Send a message to the server as a JSON string
function sendMessage(message) {
  if (websocket && websocket.readyState == WebSocket.OPEN) {
    const messageJson = JSON.stringify(message);
    websocket.send(messageJson);
  }
}

// Decode Base64 data to Array
function base64ToArray(base64) {
  const binaryString = window.atob(base64);
  const len = binaryString.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) {
    bytes[i] = binaryString.charCodeAt(i);
  }
  return bytes.buffer;
}

/**
 * Audio handling
 */

let audioPlayerNode;
let audioPlayerContext;
let audioRecorderNode;
let audioRecorderContext;
let micStream;
let isRecording = false;

// Import the audio worklets
import { startAudioPlayerWorklet } from "./audio-player.js";
import { startAudioRecorderWorklet } from "./audio-recorder.js";

// Start audio
function startAudio() {
  // Start audio output
  startAudioPlayerWorklet().then(([node, ctx]) => {
    audioPlayerNode = node;
    audioPlayerContext = ctx;
  });
  // Start audio input
  startAudioRecorderWorklet(audioRecorderHandler).then(
    ([node, ctx, stream]) => {
      audioRecorderNode = node;
      audioRecorderContext = ctx;
      micStream = stream;
      isRecording = true;
    }
  );
}

// Stop audio recording
function stopAudio() {
  if (audioRecorderNode) {
    audioRecorderNode.disconnect();
    audioRecorderNode = null;
  }

  if (audioRecorderContext) {
    audioRecorderContext
      .close()
      .catch((err) => console.error("Error closing audio context:", err));
    audioRecorderContext = null;
  }

  if (micStream) {
    micStream.getTracks().forEach((track) => track.stop());
    micStream = null;
  }

  isRecording = false;
}

// Start the audio only when the user clicked the button
// (due to the gesture requirement for the Web Audio API)
startAudioButton.addEventListener("click", () => {
  startAudioButton.disabled = true;
  startAudioButton.textContent = "Voice Enabled";
  startAudioButton.style.display = "none";
  stopAudioButton.style.display = "inline-block";
  recordingContainer.style.display = "flex";
  startAudio();
  is_audio = true;

  // Add class to messages container to enable audio styling
  messagesDiv.classList.add("audio-enabled");

  connectWebsocket(); // reconnect with the audio mode
});

// Stop audio recording when stop button is clicked
stopAudioButton.addEventListener("click", () => {
  console.log("Stop Audio button clicked.");
  stopAudio();

  // UI changes
  stopAudioButton.style.display = "none";
  startAudioButton.style.display = "inline-block";
  startAudioButton.disabled = false;
  startAudioButton.textContent = "Enable Voice";
  recordingContainer.style.display = "none";
  messagesDiv.classList.remove("audio-enabled");

  // Send a control message to the server indicating audio input is complete
  // console.log("Sending audio_input_complete to server.");
  // sendMessage({
  //   mime_type: "application/json",
  //   type: "control",
  //   data: "audio_input_complete",
  //   role: "user"
  // });

  // Remove audio styling class
  // messagesDiv.classList.remove("audio-enabled");

  // Reconnect without audio mode
  // is_audio = false;

  // Only reconnect if the connection is still open
  // if (websocket && websocket.readyState === WebSocket.OPEN) {
  //  websocket.close();
    // The onclose handler will trigger reconnection
  // }

  typingIndicator.classList.add("visible"); // Good: show server is processing the utterance
  // is_audio = false; // DO NOT CHANGE THIS HERE. The current session is still an "audio input" session.
                    // The user might want to speak again.
});

// Audio recorder handler
function audioRecorderHandler(pcmData) {
  // Only send data if we're still recording
  if (!isRecording) return;

  const base64AudioData = arrayBufferToBase64(pcmData);
  console.log(`[AudioRecorderHandler]: Captured PCM data, length: ${pcmData.byteLength}, Base64 length: ${base64AudioData.length}. Preparing to send.`); // ALWAYS LOG THIS

  // Send the pcm data as base64
  sendMessage({
    mime_type: "audio/pcm",
    data: base64AudioData,
  });

  // Log every few samples to avoid flooding the console
  if (Math.random() < 0.01) {
    // Only log ~1% of audio chunks
    console.log("[CLIENT TO AGENT] sent audio data");
  }
}

// Encode an array buffer with Base64
function arrayBufferToBase64(buffer) {
  let binary = "";
  const bytes = new Uint8Array(buffer);
  const len = bytes.byteLength;
  for (let i = 0; i < len; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return window.btoa(binary);
}