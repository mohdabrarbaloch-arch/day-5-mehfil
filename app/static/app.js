/* Mehfil frontend — vanilla JS, no build step.
 *
 * Security note: model output is rendered with textContent, never innerHTML.
 * A character card is user-supplied content and a model reply is untrusted
 * text; treating either as HTML would be an XSS hole.
 */

(() => {
  "use strict";

  const API = "";
  const state = {
    token: localStorage.getItem("mehfil_token") || "",
    user: null,
    characters: [],
    activeCharacter: null,
    conversation: null,
    loreDraft: [],
    editingId: null,
  };

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  // ───────────────────────────── helpers ─────────────────────────────

  function toast(message, bad = false) {
    const el = $("#toast");
    el.textContent = message;
    el.classList.toggle("bad", bad);
    el.classList.remove("hidden");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.add("hidden"), 3200);
  }

  async function api(path, options = {}) {
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (state.token) headers.Authorization = `Bearer ${state.token}`;

    const response = await fetch(API + path, { ...options, headers });

    if (response.status === 204) return null;

    let body = null;
    const text = await response.text();
    if (text) {
      try { body = JSON.parse(text); } catch { body = { detail: text }; }
    }

    if (!response.ok) {
      const detail = body && body.detail;
      const message = typeof detail === "string"
        ? detail
        : Array.isArray(detail) && detail[0] && detail[0].msg
          ? detail[0].msg
          : `Request failed (${response.status})`;
      const error = new Error(message);
      error.status = response.status;
      throw error;
    }
    return body;
  }

  function showView(name) {
    $$(".view").forEach((v) => v.classList.remove("active"));
    $(`#view-${name}`).classList.add("active");
  }

  function initials(name) {
    return (name || "?").trim().charAt(0).toUpperCase();
  }

  // ───────────────────────────── auth ─────────────────────────────

  $$("[data-auth-tab]").forEach((tab) => {
    tab.addEventListener("click", () => {
      $$("[data-auth-tab]").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      const which = tab.dataset.authTab;
      $("#form-login").classList.toggle("hidden", which !== "login");
      $("#form-register").classList.toggle("hidden", which !== "register");
      $("#auth-error").classList.add("hidden");
    });
  });

  function authError(message) {
    const el = $("#auth-error");
    el.textContent = message;
    el.classList.remove("hidden");
  }

  $("#form-login").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.target);
    try {
      const data = await api("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({
          email: form.get("email"),
          password: form.get("password"),
        }),
      });
      await onAuthenticated(data);
    } catch (error) {
      authError(error.message);
    }
  });

  $("#form-register").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.target);
    try {
      const data = await api("/api/auth/register", {
        method: "POST",
        body: JSON.stringify({
          email: form.get("email"),
          password: form.get("password"),
          display_name: form.get("display_name") || "",
        }),
      });
      await onAuthenticated(data);
    } catch (error) {
      authError(error.message);
    }
  });

  async function onAuthenticated(data) {
    state.token = data.access_token;
    state.user = data.user;
    localStorage.setItem("mehfil_token", state.token);
    showView("app");
    await boot();
  }

  $("#btn-logout").addEventListener("click", () => {
    state.token = "";
    state.user = null;
    state.characters = [];
    state.activeCharacter = null;
    state.conversation = null;
    localStorage.removeItem("mehfil_token");
    showView("auth");
  });

  // ───────────────────────────── boot ─────────────────────────────

  async function boot() {
    try {
      const health = await api("/api/health");
      const pill = $("#backend-pill");
      pill.textContent = `backend: ${health.backend}`;
      pill.classList.toggle("ok", health.backend !== "none");
      pill.classList.toggle("warn", health.backend === "none");
      pill.title = health.backend === "none"
        ? "No model connected — replies are labelled simulated."
        : `Generating via ${health.backend}`;
    } catch { /* health is informational */ }

    await loadCharacters();
  }

  async function loadCharacters() {
    try {
      state.characters = await api("/api/characters");
    } catch (error) {
      if (error.status === 401) { showView("auth"); return; }
      toast(error.message, true);
      return;
    }
    renderCharacters();
  }

  function renderCharacters() {
    const list = $("#char-list");
    list.replaceChildren();
    $("#char-empty").classList.toggle("hidden", state.characters.length > 0);

    state.characters.forEach((character) => {
      const li = document.createElement("li");
      li.className = "char-item";
      if (state.activeCharacter && state.activeCharacter.id === character.id) {
        li.classList.add("active");
      }

      const avatar = document.createElement("div");
      avatar.className = "char-avatar";
      avatar.textContent = initials(character.name);

      const meta = document.createElement("div");
      meta.className = "char-meta";
      const name = document.createElement("div");
      name.className = "char-name";
      name.textContent = character.name;
      const desc = document.createElement("div");
      desc.className = "char-desc";
      desc.textContent = character.description || "No description";
      meta.append(name, desc);

      li.append(avatar, meta);
      li.addEventListener("click", () => openCharacter(character.id));
      list.append(li);
    });
  }

  // ───────────────────────────── chat ─────────────────────────────

  async function openCharacter(id) {
    try {
      const character = await api(`/api/characters/${id}`);
      state.activeCharacter = character;
      renderCharacters();

      $("#pane-empty").classList.add("hidden");
      $("#pane-editor").classList.add("hidden");
      $("#pane-chat").classList.remove("hidden");
      // Collapse the character list on mobile so the composer stays on screen.
      document.querySelector(".layout").classList.add("chat-open");

      $("#chat-name").textContent = character.name;
      $("#chat-sub").textContent = character.description
        ? character.description.slice(0, 120)
        : "No description";

      // Reuse the most recent conversation with this character if one exists.
      const conversations = await api("/api/conversations");
      const existing = conversations.find((c) => c.character_id === character.id);

      if (existing) {
        state.conversation = await api(`/api/conversations/${existing.id}`);
      } else {
        state.conversation = await api("/api/conversations", {
          method: "POST",
          body: JSON.stringify({ character_id: character.id }),
        });
      }
      renderMessages();
    } catch (error) {
      toast(error.message, true);
    }
  }

  function renderMessages() {
    const container = $("#messages");
    container.replaceChildren();
    if (!state.conversation) return;

    state.conversation.messages.forEach((message) => {
      container.append(buildMessage(message));
    });
    container.scrollTop = container.scrollHeight;
  }

  function buildMessage(message) {
    const wrap = document.createElement("div");
    wrap.className = `msg ${message.role}`;
    if (message.simulated) wrap.classList.add("simulated");

    const bubble = document.createElement("div");
    bubble.className = "bubble";
    // textContent, never innerHTML — model output is untrusted.
    bubble.textContent = message.content;
    wrap.append(bubble);

    if (message.role === "assistant" && message.backend && message.backend !== "card") {
      const meta = document.createElement("div");
      meta.className = "msg-meta";

      if (message.fidelity_label) {
        const fid = document.createElement("span");
        fid.className = `fid ${message.fidelity_label}`;
        fid.textContent = message.fidelity_label === "unscored"
          ? "unscored"
          : `${message.fidelity_score} · ${message.fidelity_label.replace("_", " ")}`;
        meta.append(fid);
      }

      if (message.simulated) {
        const sim = document.createElement("span");
        sim.className = "sim-tag";
        sim.textContent = "SIMULATED";
        meta.append(sim);
      }

      const backend = document.createElement("span");
      backend.textContent = message.model
        ? `${message.backend}/${message.model}`
        : message.backend;
      meta.append(backend);

      if (message.prompt_tokens) {
        const tok = document.createElement("span");
        tok.textContent = `${message.prompt_tokens} tok`;
        meta.append(tok);
      }
      if (message.latency_ms) {
        const lat = document.createElement("span");
        lat.textContent = `${message.latency_ms}ms`;
        meta.append(lat);
      }
      wrap.append(meta);
    }
    return wrap;
  }

  const input = $("#input-message");
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 140)}px`;
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      $("#form-send").requestSubmit();
    }
  });

  $("#form-send").addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text || !state.conversation) return;

    const button = $("#btn-send");
    button.disabled = true;
    input.value = "";
    input.style.height = "auto";

    // Optimistic user bubble.
    const container = $("#messages");
    container.append(buildMessage({ role: "user", content: text }));
    container.scrollTop = container.scrollHeight;

    const pending = document.createElement("div");
    pending.className = "msg assistant";
    const pendingBubble = document.createElement("div");
    pendingBubble.className = "bubble";
    pendingBubble.textContent = "…";
    pending.append(pendingBubble);
    container.append(pending);
    container.scrollTop = container.scrollHeight;

    try {
      const result = await api(`/api/conversations/${state.conversation.id}/messages`, {
        method: "POST",
        body: JSON.stringify({ content: text }),
      });
      pending.remove();
      container.append(buildMessage(result.message));
      container.scrollTop = container.scrollHeight;

      if (result.simulated) {
        $("#chat-hint").textContent =
          "No model connected — this reply is labelled simulated. Connect Ollama or an OpenAI-compatible endpoint for real generation.";
      } else if (result.fidelity && result.fidelity.label === "drifting") {
        $("#chat-hint").textContent =
          "Voice is drifting. Open the prompt inspector to see which block changed.";
      } else {
        $("#chat-hint").textContent = "";
      }
    } catch (error) {
      pending.remove();
      toast(error.message, true);
    } finally {
      button.disabled = false;
      input.focus();
    }
  });

  // ───────────────────────────── inspector ─────────────────────────────

  $("#btn-inspect").addEventListener("click", async () => {
    if (!state.activeCharacter) return;
    try {
      const preview = await api(`/api/characters/${state.activeCharacter.id}/preview`, {
        method: "POST",
        body: JSON.stringify({ content: input.value.trim() || "(next turn)" }),
      });
      renderInspector(preview);
      $("#modal-inspect").classList.remove("hidden");
    } catch (error) {
      toast(error.message, true);
    }
  });

  $("#btn-close-inspect").addEventListener("click", () => {
    $("#modal-inspect").classList.add("hidden");
  });
  $("#modal-inspect").addEventListener("click", (event) => {
    if (event.target.id === "modal-inspect") {
      $("#modal-inspect").classList.add("hidden");
    }
  });

  function renderInspector(preview) {
    const body = $("#inspect-body");
    body.replaceChildren();

    const fit = preview.fit;
    const grid = document.createElement("div");
    grid.className = "fit-grid";

    const cells = [
      ["budget", fit.budget],
      ["used", fit.used],
      ["reserved", fit.reserved_for_reply],
      ["remaining", fit.remaining],
      ["history kept", fit.history_messages_kept],
      ["history dropped", fit.history_messages_dropped],
      ["utilisation", `${Math.round(fit.utilisation * 100)}%`],
      ["fingerprint", preview.fingerprint],
    ];
    cells.forEach(([key, value]) => {
      const cell = document.createElement("div");
      cell.className = "fit-cell";
      const k = document.createElement("div");
      k.className = "k";
      k.textContent = key;
      const v = document.createElement("div");
      v.className = "v";
      v.textContent = value;
      cell.append(k, v);
      grid.append(cell);
    });
    body.append(grid);

    const meter = document.createElement("div");
    const pct = Math.min(100, Math.round(fit.utilisation * 100));
    meter.className = "meter" + (pct >= 95 ? " danger" : pct >= 75 ? " warn" : "");
    const fill = document.createElement("i");
    fill.style.width = `${pct}%`;
    meter.append(fill);
    body.append(meter);

    if (fit.truncated_blocks.length) {
      const note = document.createElement("p");
      note.className = "small";
      note.style.color = "var(--amber)";
      note.textContent = `Truncated to fit: ${fit.truncated_blocks.join(", ")}`;
      body.append(note);
    }
    if (fit.dropped_lore.length) {
      const note = document.createElement("p");
      note.className = "small";
      note.style.color = "var(--amber)";
      note.textContent = `Lore dropped for budget: ${fit.dropped_lore.join(", ")}`;
      body.append(note);
    }

    preview.blocks.forEach((block) => {
      const wrap = document.createElement("div");
      wrap.className = "block" + (block.content ? "" : " empty");

      const head = document.createElement("div");
      head.className = "block-head";
      const label = document.createElement("span");
      label.textContent = block.name;
      const tok = document.createElement("span");
      tok.className = "tok";
      tok.textContent = block.truncated
        ? `${block.tokens} tok · truncated`
        : `${block.tokens} tok`;
      if (block.truncated) tok.classList.add("cut");
      head.append(label, tok);

      const content = document.createElement("div");
      content.className = "block-body";
      content.textContent = block.content || "(empty — nothing to send)";

      wrap.append(head, content);
      body.append(wrap);
    });
  }

  // ───────────────────────────── editor ─────────────────────────────

  function openEditor(character) {
    state.editingId = character ? character.id : null;
    state.loreDraft = character ? (character.lore || []).map((e) => ({ ...e })) : [];

    $("#pane-empty").classList.add("hidden");
    $("#pane-chat").classList.add("hidden");
    $("#pane-editor").classList.remove("hidden");
    document.querySelector(".layout").classList.add("chat-open");
    $("#editor-title").textContent = character ? `Edit ${character.name}` : "New character";

    const form = $("#form-char");
    form.reset();

    if (character) {
      form.name.value = character.name || "";
      form.description.value = character.description || "";
      form.personality.value = character.personality || "";
      form.scenario.value = character.scenario || "";
      form.first_mes.value = character.first_mes || "";
      form.mes_example.value = character.mes_example || "";
      form.system_prompt.value = character.system_prompt || "";
      form.post_history_instructions.value = character.post_history_instructions || "";
      form.alternate_greetings.value = (character.alternate_greetings || []).join("\n");
      form.tags.value = (character.tags || []).join(", ");
      form.is_public.checked = !!character.is_public;
    }
    renderLore();
  }

  function renderLore() {
    const list = $("#lore-list");
    list.replaceChildren();

    state.loreDraft.forEach((entry, index) => {
      const wrap = document.createElement("div");
      wrap.className = "lore-entry";

      const row1 = document.createElement("div");
      row1.className = "row";
      const nameInput = document.createElement("input");
      nameInput.type = "text";
      nameInput.placeholder = "Entry name";
      nameInput.value = entry.name || "";
      nameInput.addEventListener("input", (e) => { entry.name = e.target.value; });

      const priorityInput = document.createElement("input");
      priorityInput.type = "number";
      priorityInput.min = "0";
      priorityInput.max = "100";
      priorityInput.value = entry.priority ?? 10;
      priorityInput.title = "Priority (higher injects first)";
      priorityInput.addEventListener("input", (e) => {
        entry.priority = Number(e.target.value) || 0;
      });

      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "btn ghost small";
      remove.textContent = "Remove";
      remove.addEventListener("click", () => {
        state.loreDraft.splice(index, 1);
        renderLore();
      });

      row1.append(nameInput, priorityInput, remove);

      const keysInput = document.createElement("input");
      keysInput.type = "text";
      keysInput.placeholder = "Trigger keys, comma separated";
      keysInput.value = (entry.keys || []).join(", ");
      keysInput.addEventListener("input", (e) => {
        entry.keys = e.target.value.split(",").map((k) => k.trim()).filter(Boolean);
      });

      const contentInput = document.createElement("textarea");
      contentInput.rows = 2;
      contentInput.placeholder = "What the model should know when this triggers";
      contentInput.value = entry.content || "";
      contentInput.addEventListener("input", (e) => { entry.content = e.target.value; });

      const row2 = document.createElement("div");
      row2.className = "row";
      const enabledLabel = document.createElement("label");
      enabledLabel.className = "check";
      const enabledBox = document.createElement("input");
      enabledBox.type = "checkbox";
      enabledBox.checked = entry.enabled !== false;
      enabledBox.addEventListener("change", (e) => { entry.enabled = e.target.checked; });
      enabledLabel.append(enabledBox, document.createTextNode("Enabled"));

      const constantLabel = document.createElement("label");
      constantLabel.className = "check";
      const constantBox = document.createElement("input");
      constantBox.type = "checkbox";
      constantBox.checked = !!entry.constant;
      constantBox.addEventListener("change", (e) => { entry.constant = e.target.checked; });
      constantLabel.append(constantBox, document.createTextNode("Always inject"));

      row2.append(enabledLabel, constantLabel);

      wrap.append(row1, keysInput, contentInput, row2);
      list.append(wrap);
    });
  }

  $("#btn-add-lore").addEventListener("click", () => {
    state.loreDraft.push({
      name: "",
      keys: [],
      content: "",
      priority: 10,
      enabled: true,
      constant: false,
      case_sensitive: false,
    });
    renderLore();
  });

  $("#btn-new-char").addEventListener("click", () => openEditor(null));
  $("#btn-new-char-2").addEventListener("click", () => openEditor(null));
  $("#btn-edit-char").addEventListener("click", () => {
    if (state.activeCharacter) openEditor(state.activeCharacter);
  });
  $("#btn-cancel-edit").addEventListener("click", () => {
    if (state.activeCharacter) {
      openCharacter(state.activeCharacter.id);
    } else {
      $("#pane-editor").classList.add("hidden");
      $("#pane-empty").classList.remove("hidden");
      document.querySelector(".layout").classList.remove("chat-open");
    }
  });

  $("#btn-save-char").addEventListener("click", async () => {
    const form = $("#form-char");
    // `form.name` is the form's own name attribute, not the input — always go
    // through `form.elements` for named controls.
    const el = form.elements;
    if (!el.name.value.trim()) {
      toast("A character needs a name.", true);
      el.name.focus();
      return;
    }

    const payload = {
      name: el.name.value.trim(),
      description: el.description.value,
      personality: el.personality.value,
      scenario: el.scenario.value,
      first_mes: el.first_mes.value,
      mes_example: el.mes_example.value,
      system_prompt: el.system_prompt.value,
      post_history_instructions: el.post_history_instructions.value,
      alternate_greetings: el.alternate_greetings.value
        .split("\n").map((s) => s.trim()).filter(Boolean),
      tags: el.tags.value.split(",").map((s) => s.trim()).filter(Boolean),
      is_public: el.is_public.checked,
      lore: state.loreDraft
        .filter((entry) => entry.content && entry.content.trim())
        .map((entry) => ({
          name: entry.name || "",
          keys: entry.keys || [],
          content: entry.content,
          priority: entry.priority ?? 10,
          enabled: entry.enabled !== false,
          constant: !!entry.constant,
          case_sensitive: !!entry.case_sensitive,
        })),
    };

    try {
      let saved;
      if (state.editingId) {
        saved = await api(`/api/characters/${state.editingId}`, {
          method: "PUT",
          body: JSON.stringify(payload),
        });
      } else {
        saved = await api("/api/characters", {
          method: "POST",
          body: JSON.stringify(payload),
        });
      }
      toast(state.editingId ? "Character updated." : "Character created.");
      await loadCharacters();
      await openCharacter(saved.id);
    } catch (error) {
      toast(error.message, true);
    }
  });

  // ───────────────────────────── import ─────────────────────────────

  $("#btn-import").addEventListener("click", () => $("#file-import").click());

  $("#file-import").addEventListener("change", async (event) => {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    if (file.size > 512000) {
      toast("Card file is too large (max 500 KB).", true);
      return;
    }
    try {
      const text = await file.text();
      const parsed = JSON.parse(text);
      const result = await api("/api/characters/import", {
        method: "POST",
        body: JSON.stringify({ card: parsed }),
      });
      toast(
        result.warnings.length
          ? `Imported with ${result.warnings.length} warning(s).`
          : "Card imported."
      );
      await loadCharacters();
      await openCharacter(result.character.id);
    } catch (error) {
      toast(error.message || "Could not read that card.", true);
    } finally {
      event.target.value = "";
    }
  });

  // ───────────────────────────── start ─────────────────────────────

  (async function start() {
    if (!state.token) { showView("auth"); return; }
    try {
      state.user = await api("/api/auth/me");
      showView("app");
      await boot();
    } catch {
      state.token = "";
      localStorage.removeItem("mehfil_token");
      showView("auth");
    }
  })();
})();
