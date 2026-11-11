(function () {
    "use strict";

    // ------------------------------------------------------------------
    // Element-lər
    // ------------------------------------------------------------------
    const chatForm = document.getElementById("chatForm");
    const messageInput = document.getElementById("messageInput");
    const chatMessages = document.getElementById("chatMessages");
    const sendButton = document.getElementById("sendButton");
    const sendLabel = document.getElementById("sendLabel");
    const sendIcon = document.getElementById("sendIcon");
    const attachButton = document.getElementById("attachButton");
    const fileInput = document.getElementById("fileInput");
    const attachmentsBox = document.getElementById("attachments");
    const dropOverlay = document.getElementById("dropOverlay");
    const newChatButton = document.getElementById("newChatButton");
    const statusDot = document.getElementById("statusDot");
    const statusText = document.getElementById("statusText");
    const toast = document.getElementById("toast");

    // ------------------------------------------------------------------
    // Limitlər (backend ilə eyni olmalıdır)
    // ------------------------------------------------------------------
    const MAX_IMAGES = 4;
    const MAX_BYTES = 3.5 * 1024 * 1024;
    const MAX_DIMENSION = 1568; // AI modelləri üçün kifayət qədər yüksək keyfiyyət
    const ALLOWED_TYPES = ["image/jpeg", "image/png", "image/gif", "image/webp"];

    let pendingImages = []; // { file, url }
    let busy = false;
    let controller = null;
    let toastTimer = null;

    // ------------------------------------------------------------------
    // Kiçik köməkçilər
    // ------------------------------------------------------------------
    function showToast(text) {
        toast.textContent = text;
        toast.hidden = false;
        clearTimeout(toastTimer);
        toastTimer = setTimeout(function () { toast.hidden = true; }, 4000);
    }

    function nearBottom() {
        return chatMessages.scrollHeight - chatMessages.scrollTop - chatMessages.clientHeight < 120;
    }

    function scrollToBottom(force) {
        if (force || nearBottom()) chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    function autoGrow() {
        messageInput.style.height = "auto";
        messageInput.style.height = Math.min(messageInput.scrollHeight, 160) + "px";
    }

    function hideWelcome() {
        const welcome = document.getElementById("welcome");
        if (welcome) welcome.remove();
    }

    function setBusy(value) {
        busy = value;
        sendLabel.textContent = value ? "Dayandır" : "Göndər";
        sendIcon.textContent = value ? "■" : "➤";
        sendButton.classList.toggle("stop", value);
        attachButton.disabled = value;
        newChatButton.disabled = value;
    }

    // ------------------------------------------------------------------
    // Server statusu
    // ------------------------------------------------------------------
    async function checkHealth() {
        try {
            const res = await fetch("/api/health");
            const data = await res.json();
            if (data.configured) {
                statusDot.className = "status-dot";
                statusText.textContent = "Online";
            } else {
                statusDot.className = "status-dot warn";
                statusText.textContent = "API açarı yoxdur";
            }
        } catch (e) {
            statusDot.className = "status-dot off";
            statusText.textContent = "Offline";
        }
    }

    // ------------------------------------------------------------------
    // Mesajların göstərilməsi
    // ------------------------------------------------------------------
    function createMessage(sender) {
        const message = document.createElement("div");
        message.classList.add("message", sender + "-message");

        if (sender === "bot") {
            const avatar = document.createElement("div");
            avatar.classList.add("avatar");
            avatar.textContent = "✦";
            message.appendChild(avatar);
        }

        const wrap = document.createElement("div");
        wrap.classList.add("message-body");
        const content = document.createElement("div");
        content.classList.add("message-content");
        wrap.appendChild(content);
        message.appendChild(wrap);

        chatMessages.appendChild(message);
        return { message: message, wrap: wrap, content: content };
    }

    function addUserMessage(text, images) {
        hideWelcome();
        const m = createMessage("user");

        if (images.length) {
            const strip = document.createElement("div");
            strip.classList.add("sent-images");
            images.forEach(function (img) {
                const el = document.createElement("img");
                el.src = img.url;
                el.alt = "Göndərilən şəkil";
                strip.appendChild(el);
            });
            m.content.appendChild(strip);
        }
        if (text) {
            const p = document.createElement("div");
            p.classList.add("user-text");
            p.textContent = text; // təhlükəsiz: yalnız mətn kimi
            m.content.appendChild(p);
        }
        scrollToBottom(true);
    }

    function addBotMessage() {
        hideWelcome();
        const m = createMessage("bot");
        m.content.innerHTML = '<span class="typing" aria-label="Cavab yazılır"><i></i><i></i><i></i></span>';
        scrollToBottom(true);
        return m;
    }

    function addCopyButton(m, getText) {
        const actions = document.createElement("div");
        actions.classList.add("message-actions");
        const btn = document.createElement("button");
        btn.type = "button";
        btn.classList.add("action-button");
        btn.textContent = "Kopyala";
        btn.addEventListener("click", async function () {
            try {
                await navigator.clipboard.writeText(getText());
                btn.textContent = "Kopyalandı ✓";
            } catch (e) {
                btn.textContent = "Alınmadı";
            }
            setTimeout(function () { btn.textContent = "Kopyala"; }, 1800);
        });
        actions.appendChild(btn);
        m.wrap.appendChild(actions);
    }

    function showError(m, text) {
        m.content.classList.add("error");
        m.content.textContent = text;
    }

    // ------------------------------------------------------------------
    // Şəkillər
    // ------------------------------------------------------------------
    function renderAttachments() {
        attachmentsBox.innerHTML = "";
        attachmentsBox.hidden = pendingImages.length === 0;
        pendingImages.forEach(function (img, index) {
            const item = document.createElement("div");
            item.classList.add("attachment");

            const el = document.createElement("img");
            el.src = img.url;
            el.alt = "Əlavə olunan şəkil";

            const remove = document.createElement("button");
            remove.type = "button";
            remove.classList.add("attachment-remove");
            remove.setAttribute("aria-label", "Şəkli sil");
            remove.textContent = "×";
            remove.addEventListener("click", function () {
                URL.revokeObjectURL(img.url);
                pendingImages.splice(index, 1);
                renderAttachments();
            });

            item.appendChild(el);
            item.appendChild(remove);
            attachmentsBox.appendChild(item);
        });
    }

    function loadBitmap(file) {
        if (window.createImageBitmap) return createImageBitmap(file);
        return new Promise(function (resolve, reject) {
            const url = URL.createObjectURL(file);
            const img = new Image();
            img.onload = function () { URL.revokeObjectURL(url); resolve(img); };
            img.onerror = function () { URL.revokeObjectURL(url); reject(new Error("load")); };
            img.src = url;
        });
    }

    function canvasToBlob(canvas, quality) {
        return new Promise(function (resolve) { canvas.toBlob(resolve, "image/jpeg", quality); });
    }

    // Böyük şəkilləri kiçildir: sürətli yükləmə və 3,5 MB limitinə uyğunluq üçün
    async function prepareImage(file) {
        if (ALLOWED_TYPES.indexOf(file.type) === -1) {
            throw new Error("“" + file.name + "” dəstəklənmir. Yalnız JPG, PNG, GIF və WEBP olar.");
        }
        if (file.type === "image/gif" && file.size <= MAX_BYTES) return file;

        let bitmap;
        try { bitmap = await loadBitmap(file); } catch (e) {
            throw new Error("“" + file.name + "” oxuna bilmədi.");
        }

        const longest = Math.max(bitmap.width, bitmap.height);
        const scale = Math.min(1, MAX_DIMENSION / longest);
        if (scale === 1 && file.size <= MAX_BYTES) return file;

        const canvas = document.createElement("canvas");
        canvas.width = Math.max(1, Math.round(bitmap.width * scale));
        canvas.height = Math.max(1, Math.round(bitmap.height * scale));
        const ctx = canvas.getContext("2d");
        ctx.fillStyle = "#ffffff"; // şəffaf PNG-lər JPEG-də qara olmasın
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);

        let blob = await canvasToBlob(canvas, 0.88);
        if (blob && blob.size > MAX_BYTES) blob = await canvasToBlob(canvas, 0.7);
        if (!blob || blob.size > MAX_BYTES) {
            throw new Error("“" + file.name + "” çox böyükdür, kiçildilə bilmədi.");
        }
        const name = file.name.replace(/\.[^.]+$/, "") + ".jpg";
        return new File([blob], name, { type: "image/jpeg" });
    }

    async function addFiles(fileList) {
        const files = Array.prototype.slice.call(fileList).filter(function (f) {
            return f.type.indexOf("image/") === 0;
        });
        if (!files.length) {
            showToast("Yalnız şəkil faylları qəbul olunur.");
            return;
        }
        for (const file of files) {
            if (pendingImages.length >= MAX_IMAGES) {
                showToast("Eyni anda maksimum " + MAX_IMAGES + " şəkil əlavə etmək olar.");
                break;
            }
            try {
                const ready = await prepareImage(file);
                pendingImages.push({ file: ready, url: URL.createObjectURL(ready) });
                renderAttachments();
            } catch (err) {
                showToast(err.message);
            }
        }
        messageInput.focus();
    }

    // ------------------------------------------------------------------
    // Göndərmə və axın (streaming)
    // ------------------------------------------------------------------
    async function sendMessage() {
        const text = messageInput.value.trim();
        if (!text && !pendingImages.length) return;

        const images = pendingImages.slice();
        pendingImages = [];
        renderAttachments();
        messageInput.value = "";
        autoGrow();

        addUserMessage(text, images);
        const bot = addBotMessage();
        setBusy(true);
        controller = new AbortController();

        const form = new FormData();
        form.append("message", text);
        images.forEach(function (img) { form.append("images", img.file, img.file.name); });

        let fullText = "";
        let failed = false;

        try {
            const response = await fetch("/api/chat", { method: "POST", body: form, signal: controller.signal });

            if (!response.ok) {
                let msg = "Xəta baş verdi.";
                try { msg = (await response.json()).error || msg; } catch (e) { /* ignore */ }
                throw new Error(msg);
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = "";

            while (true) {
                const result = await reader.read();
                if (result.done) break;
                buffer += decoder.decode(result.value, { stream: true });

                let sep;
                while ((sep = buffer.indexOf("\n\n")) !== -1) {
                    const raw = buffer.slice(0, sep);
                    buffer = buffer.slice(sep + 2);
                    if (raw.indexOf("data: ") !== 0) continue;

                    const event = JSON.parse(raw.slice(6));
                    if (event.type === "delta") {
                        fullText += event.text;
                        bot.content.innerHTML = renderMarkdown(fullText);
                        scrollToBottom(false);
                    } else if (event.type === "error") {
                        throw new Error(event.message);
                    }
                }
            }
        } catch (err) {
            if (err.name === "AbortError") {
                if (!fullText) bot.message.remove();
            } else {
                failed = true;
                if (fullText) {
                    showToast(err.message);
                } else {
                    showError(bot, err.message || "Bağlantıda problem yarandı. Bir daha cəhd et.");
                }
            }
        } finally {
            if (fullText) addCopyButton(bot, function () { return fullText; });
            controller = null;
            setBusy(false);
            messageInput.focus();
            scrollToBottom(false);
        }
    }

    // ------------------------------------------------------------------
    // Hadisələr
    // ------------------------------------------------------------------
    chatForm.addEventListener("submit", function (event) {
        event.preventDefault();
        if (busy) {
            if (controller) controller.abort();
            return;
        }
        sendMessage();
    });

    messageInput.addEventListener("keydown", function (event) {
        if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
            event.preventDefault();
            if (!busy) sendMessage();
        }
    });
    messageInput.addEventListener("input", autoGrow);

    attachButton.addEventListener("click", function () { fileInput.click(); });
    fileInput.addEventListener("change", function () {
        if (fileInput.files.length) addFiles(fileInput.files);
        fileInput.value = "";
    });

    // Yapışdırma (Ctrl+V ilə şəkil)
    document.addEventListener("paste", function (event) {
        const items = (event.clipboardData && event.clipboardData.files) || [];
        if (items.length && !busy) {
            event.preventDefault();
            addFiles(items);
        }
    });

    // Sürüklə-burax
    let dragDepth = 0;
    function hasFiles(event) {
        return event.dataTransfer && Array.prototype.indexOf.call(event.dataTransfer.types || [], "Files") !== -1;
    }
    document.addEventListener("dragenter", function (event) {
        if (!hasFiles(event)) return;
        dragDepth++;
        dropOverlay.hidden = false;
    });
    document.addEventListener("dragleave", function (event) {
        if (!hasFiles(event)) return;
        dragDepth = Math.max(0, dragDepth - 1);
        if (!dragDepth) dropOverlay.hidden = true;
    });
    document.addEventListener("dragover", function (event) {
        if (hasFiles(event)) event.preventDefault();
    });
    document.addEventListener("drop", function (event) {
        if (!hasFiles(event)) return;
        event.preventDefault();
        dragDepth = 0;
        dropOverlay.hidden = true;
        if (!busy) addFiles(event.dataTransfer.files);
    });

    // Təklif düymələri
    chatMessages.addEventListener("click", function (event) {
        const chip = event.target.closest(".chip");
        if (!chip) return;
        if (chip.dataset.attach) {
            fileInput.click();
        } else if (chip.dataset.prompt) {
            messageInput.value = chip.dataset.prompt;
            autoGrow();
            sendMessage();
        }
    });

    // Yeni söhbət
    newChatButton.addEventListener("click", async function () {
        if (busy) return;
        try { await fetch("/api/reset", { method: "POST" }); } catch (e) { /* ignore */ }
        pendingImages.forEach(function (img) { URL.revokeObjectURL(img.url); });
        pendingImages = [];
        renderAttachments();
        location.reload();
    });

    checkHealth();
    messageInput.focus();
})();
