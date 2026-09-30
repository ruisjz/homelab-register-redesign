const componentCategories = [
  ["cpu", "CPU"],
  ["gpu", "GPU"],
  ["motherboard", "Motherboard"],
  ["ram", "RAM"],
  ["storage", "Armazenamento"],
  ["psu", "PSU"],
  ["network", "Rede"],
  ["cooler", "Cooler"],
  ["fan", "Ventoinha"],
  ["controller", "Controlador"],
  ["other", "Outro"],
];
const categoryLabel = (k) => (componentCategories.find((x) => x[0] === k) || ["other", "Outro"])[1];
const componentStatusLabel = (s) =>
  ({ ok: "Operacional", warn: "Atenção", offline: "Offline" })[s] || s || "—";
const checks = ["Limpeza interna", "Ventoinhas", "Dissipador", "Cabos", "Pasta térmica", "Estado físico"];
let state = [],
  preventivePlans = [],
  page = "dashboard",
  activeId = null,
  attachmentCounts = {},
  deferredInstallPrompt = null,
  revision = null;
const $ = (s) => document.querySelector(s);
const esc = (x) =>
  String(x ?? "").replace(
    /[&<>"']/g,
    (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[m],
  );
const equipmentById = (id) => state.find((x) => x.id === id);
function toast(msg) {
  let n = document.createElement("div");
  n.className = "toast";
  n.textContent = msg;
  document.body.append(n);
  setTimeout(() => n.remove(), 2200);
}
async function api(url, opt = {}) {
  let r = await fetch(url, { cache: "no-store", ...opt });
  let x = await r.json().catch(() => ({}));
  if (!r.ok) {
    const err = Error(x.error || "Pedido falhou");
    err.status = r.status;
    err.body = x;
    throw err;
  }
  return x;
}
let pwaRegistered = false,
  pendingWorker = null,
  reloadingForUpdate = false;
async function registerPWA() {
  if (pwaRegistered) return;
  pwaRegistered = true;
  if ("serviceWorker" in navigator) {
    try {
      const reg = await navigator.serviceWorker.register("/static/sw.js", {
        scope: "/",
        updateViaCache: "none",
      });
      // P21: uma nova versão instalada fica à espera; mostra-se o aviso em vez de recarregar sozinho.
      const offer = (worker) => {
        if (worker && navigator.serviceWorker.controller) {
          pendingWorker = worker;
          renderUpdateBanner();
        }
      };
      offer(reg.waiting);
      reg.addEventListener("updatefound", () => {
        const worker = reg.installing;
        worker?.addEventListener("statechange", () => worker.state === "installed" && offer(worker));
      });
      navigator.serviceWorker.addEventListener("controllerchange", () => {
        if (reloadingForUpdate) location.reload();
      });
      setInterval(() => reg.update().catch(() => {}), 30 * 60 * 1000);
      document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") reg.update().catch(() => {});
      });
    } catch (e) {
      console.warn("PWA service worker unavailable", e);
    }
  }
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferredInstallPrompt = e;
    updateInstallButton();
  });
  window.addEventListener("appinstalled", () => {
    deferredInstallPrompt = null;
    updateInstallButton();
  });
}
function updateInstallButton() {
  const b = document.querySelector("#pwaInstall");
  if (!b) return;
  b.hidden = !deferredInstallPrompt;
}
async function installPWA() {
  if (!deferredInstallPrompt) return;
  const p = deferredInstallPrompt;
  deferredInstallPrompt = null;
  await p.prompt();
  await p.userChoice;
  updateInstallButton();
}
async function boot() {
  registerPWA();
  try {
    if (!(await api("/api/session")).authenticated) return login();
    const payload = await api("/api/data");
    const d = payload.data;
    revision = payload.revision;
    state = d.equipment || [];
    preventivePlans = d.preventivePlans || [];
    attachmentCounts = (await api("/api/attachments/summary")).counts || {};
    const legacy = {
      equipment: "#/equipamentos",
      maintenance: "#/manutencao",
      smart: "#/smart",
      backup: "#/backup",
    }[new URLSearchParams(location.search).get("page")];
    if (legacy) history.replaceState(null, "", "/" + legacy);
    const place = takePlace();
    if (place && !location.hash && place.activeId && equipmentById(place.activeId))
      history.replaceState(null, "", `/#/equipamentos/${encodeURIComponent(place.activeId)}`);
    else if (place && !location.hash && ROUTES[place.page])
      history.replaceState(null, "", "/" + ROUTES[place.page]);
    appReady = true;
    showRoute();
  } catch (e) {
    // P20: distingue «sem ligação» de outros erros e permite tentar de novo.
    const offline = !navigator.onLine || !e.status;
    document.body.innerHTML = `<main class="fatal" role="alert"><h1>${offline ? "Sem ligação ao servidor" : "Não foi possível iniciar a aplicação"}</h1><p>${offline ? "Verifica a ligação à rede ou se o servidor está a correr." : esc(e.message || "Erro inesperado.")}</p><button class="accent" onclick="retryBoot(this)">Tentar de novo</button></main>`;
  }
}
function retryBoot(button) {
  if (button) {
    button.disabled = true;
    button.textContent = "A tentar…";
  }
  boot();
}
window.addEventListener("online", () => {
  if (document.querySelector("main.fatal")) retryBoot(document.querySelector("main.fatal button"));
});
function renderUpdateBanner() {
  if (!pendingWorker || document.querySelector("#update-banner")) return;
  const banner = document.createElement("div");
  banner.id = "update-banner";
  banner.setAttribute("role", "status");
  banner.innerHTML =
    '<span>Nova versão disponível</span><button class="accent" onclick="applyUpdate()">Atualizar</button>';
  document.body.appendChild(banner);
}
function applyUpdate() {
  if (
    modalDirty() &&
    !confirm("Tens alterações por guardar.\n\nAtualizar agora descarta essas alterações. Continuar?")
  )
    return;
  reloadingForUpdate = true;
  pendingWorker?.postMessage({ type: "SKIP_WAITING" });
}
function login(error = "") {
  document.body.innerHTML = `<main class="login"><section><div class="logo">HOMELAB <b>REGISTER</b></div><h1>O teu inventário, num só lugar.</h1><p>Hardware, manutenção, diagnósticos e histórico.</p><form onsubmit="doLogin(event)"><label>Utilizador<input id="u" autocomplete="username" required></label><label>Palavra-passe<input id="pw" type="password" autocomplete="current-password" required></label>${error ? `<div class="error">${esc(error)}</div>` : ""}<button class="accent">Entrar</button></form></section></main>`;
  renderUpdateBanner(); // P21: o aviso de nova versão sobrevive à troca de ecrã
}
async function doLogin(e) {
  e.preventDefault();
  try {
    await api("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: $("#u").value, password: $("#pw").value }),
    });
    // P14: se o browser recusou o cookie (p. ex. COOKIE_SECURE=true num acesso por http://),
    // o login "funciona" mas a sessão não fica. Explica em vez de voltar ao login em silêncio.
    const session = await api("/api/session");
    if (!session.authenticated)
      return login(
        "O login foi aceite, mas o browser não guardou o cookie de sessão. Se acedes por http://, define COOKIE_SECURE=false no .env desta instalação e reinicia o contentor (ou usa HTTPS).",
      );
    location.reload();
  } catch (x) {
    login(x.message);
  }
}
async function save() {
  const x = await api("/api/data", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ equipment: state, preventivePlans, baseRevision: revision }),
  });
  revision = x.revision;
  toast("Guardado");
}
// P03: todas as gravações passam por commit(). A alteração é aplicada em memória e
// gravada; se a gravação falhar, o estado volta ao que era e o utilizador é avisado.
async function commit(mutate) {
  const before = { state: structuredClone(state), preventivePlans: structuredClone(preventivePlans) };
  try {
    mutate();
    await save();
    return true;
  } catch (e) {
    state = before.state;
    preventivePlans = before.preventivePlans;
    handleSaveError(e);
    return false;
  }
}
function handleSaveError(e) {
  if (e.status === 409) {
    if (
      confirm(
        "Os dados foram alterados noutro dispositivo ou separador. A tua alteração não foi gravada.\n\nRecarregar agora para ver os dados atuais?\n\nSe cancelares, ficas nesta página, mas as próximas gravações também serão recusadas até recarregares.",
      )
    ) {
      rememberPlace();
      location.reload();
    }
    return;
  }
  if (e.status === 401) {
    alert(
      "A sessão terminou (por exemplo, porque o servidor foi reiniciado). A alteração não foi gravada.\n\nInicia sessão de novo e repete a alteração.",
    );
    rememberPlace();
    login();
    return;
  }
  const reason = e.status ? e.message : "Sem ligação ao servidor.";
  alert(`Não foi possível gravar: ${reason}\n\nA alteração não foi aplicada.`);
}
function rememberPlace() {
  try {
    sessionStorage.setItem("hr-return", JSON.stringify({ page, activeId }));
  } catch {}
}
function takePlace() {
  try {
    const place = JSON.parse(sessionStorage.getItem("hr-return") || "null");
    sessionStorage.removeItem("hr-return");
    return place;
  } catch {
    return null;
  }
}
async function logout() {
  await api("/api/logout", { method: "POST" });
  location.reload();
}
function shell(title, sub, actions = "") {
  return `<div class="top"><div><div class="eyebrow">HOMELAB / REGISTER</div><h1>${esc(title)}</h1><p>${esc(sub)}</p></div><div class="top-actions">${actions}</div></div>`;
}
function layout(content) {
  document.body.innerHTML = `<div class="mobilebar"><button type="button" onclick="menu()" aria-label="Abrir menu">☰</button><b>Homelab Register</b><span class="mobilebar-spacer"></span><button id="pwaInstall" class="icon-btn pwa-install" type="button" onclick="installPWA()" aria-label="Instalar aplicação" title="Instalar aplicação" hidden>⇩</button></div><aside id="side"><div class="brand"><span>H</span><div><b>Homelab</b><small>Register</small></div></div><nav>${[
    ["dashboard", "Visão geral", "◈"],
    ["equipment", "Equipamentos", "▣"],
    ["maintenance", "Manutenção", "↻"],
    ["smart", "SMART", "◉"],
    ["backup", "Dados & backup", "⇅"],
  ]
    .map(
      (x) =>
        `<button class="${page === x[0] ? "on" : ""}" onclick="go('${x[0]}')"><i>${x[2]}</i>${x[1]}</button>`,
    )
    .join(
      "",
    )}</nav><button class="logout" onclick="logout()">↪ Terminar sessão</button></aside><main class="content">${content}</main><div id="drawer"></div>`;
  updateInstallButton();
  renderUpdateBanner(); // P21: o aviso de nova versão sobrevive à troca de ecrã
}
// P19: cada página tem endereço próprio (#/...), para o «voltar» do Android/PWA,
// os atalhos da app instalada e o recarregar da página funcionarem.
const ROUTES = {
  dashboard: "#/",
  equipment: "#/equipamentos",
  maintenance: "#/manutencao",
  smart: "#/smart",
  backup: "#/backup",
};
const ROUTE_PAGES = {
  "": "dashboard",
  equipamentos: "equipment",
  manutencao: "maintenance",
  smart: "smart",
  backup: "backup",
};
let appReady = false,
  modalHistory = false,
  ignorePop = false,
  pendingHash = null,
  lastRoutedHash = null;
function currentRoute() {
  const parts = location.hash
    .replace(/^#\/?/, "")
    .split("/")
    .filter(Boolean)
    .map((x) => decodeURIComponent(x));
  return { page: ROUTE_PAGES[parts[0] || ""] || "dashboard", id: parts[1] || null, sub: parts[2] || null };
}
function showRoute() {
  const r = currentRoute();
  lastRoutedHash = location.hash;
  page = r.page;
  activeId = null;
  if (r.page === "equipment" && r.id && equipmentById(r.id)) return renderDetail(r.id);
  if (r.page === "maintenance" && r.id && r.sub) return renderMaintenanceDetail(r.id, r.sub);
  renderPage();
}
// Cada entrada do histórico criada pela app tem um número (n); n > 0 quer dizer que
// existe uma página anterior da própria app para onde voltar.
function historyDepth() {
  return Number(history.state?.n || 0);
}
function navigate(hash) {
  // Um modal acabado de fechar ainda está a retirar a sua entrada do histórico:
  // a navegação espera por isso, para não ser desfeita.
  if (ignorePop) return (pendingHash = hash);
  if (location.hash === hash || (hash === "#/" && !location.hash)) return showRoute();
  history.pushState({ n: historyDepth() + 1 }, "", hash);
  showRoute();
}
function goBack(fallback) {
  if (historyDepth() > 0) history.back();
  else navigate(fallback);
}
window.addEventListener("popstate", () => {
  if (ignorePop) {
    ignorePop = false;
    if (pendingHash) {
      const hash = pendingHash;
      pendingHash = null;
      navigate(hash);
    }
    return;
  }
  // P19: com um modal aberto, o «voltar» fecha primeiro o modal.
  if (modalHistory && document.body.classList.contains("modal-open")) {
    modalHistory = false;
    if (modalDirty() && !confirm("Tens alterações por guardar.\n\nDescartar as alterações?")) {
      history.pushState({ n: historyDepth() + 1, modal: true }, ""); // continua no modal
      modalHistory = true;
      return;
    }
    closeModal({ fromHistory: true });
    return;
  }
  if (appReady && location.hash !== lastRoutedHash) showRoute();
});
// Endereço alterado à mão (ex.: escrito na barra): também navega.
window.addEventListener("hashchange", () => {
  if (appReady && location.hash !== lastRoutedHash && !document.body.classList.contains("modal-open"))
    showRoute();
});
function go(p) {
  document.querySelector("#side")?.classList.remove("open");
  navigate(ROUTES[p] || "#/");
}
function menu() {
  $("#side").classList.toggle("open");
}
function render() {
  // Volta a desenhar a página atual (incluindo detalhes), de acordo com o endereço.
  if (appReady) return showRoute();
  renderPage();
}
function renderPage() {
  if (page === "dashboard") dashboard();
  else if (page === "equipment") equipment();
  else if (page === "maintenance") maintenance();
  else if (page === "smart") smart();
  else backup();
}
const EQUIPMENT_STATUS = {
  ok: ["Operacional", "good"],
  warn: ["Atenção", "warn"],
  offline: ["Offline", "offline"],
};
// P20: a pesquisa usa só valores que aparecem no ecrã (não os nomes internos dos campos nem IDs).
function searchText(e) {
  const own = ["name", "hostname", "role", "vendor", "model", "serial", "location", "notes", "created"]
    .concat(["cpu", "motherboard", "gpu", "nic", "psu", "bios"])
    .map((k) => e[k]);
  const components = (e.components || []).flatMap((c) => [
    c.name,
    c.model,
    c.serial,
    c.capacity,
    c.technology,
    categoryLabel(c.type),
  ]);
  const records = (e.maintenance || []).flatMap((m) => [m.date, m.type, m.description, m.areas, m.notes]);
  return [...own, equipmentStatusLabel(e.status), ramSummary(e), storageSummary(e), ...components, ...records]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}
function equipmentStatusLabel(s) {
  return EQUIPMENT_STATUS[s]?.[0] || "Sem estado";
}
function badge(s) {
  return `<span class="badge ${EQUIPMENT_STATUS[s]?.[1] || "neutral"}">${equipmentStatusLabel(s)}</span>`;
}
function attKey(type, id) {
  return `${type}:${id}`;
}
function attCount(type, id) {
  return Number(attachmentCounts[attKey(type, id)] || 0);
}
function clip(type, id) {
  let n = attCount(type, id);
  return `<button class="clip" title="${n ? `${n} anexo${n === 1 ? "" : "s"}` : "Anexos"}" aria-label="Anexos" onclick="manageAttachments('${type}','${id}')">📎${n ? `<span>${n}</span>` : ""}</button>`;
}
async function refreshAttachmentCounts() {
  attachmentCounts = (await api("/api/attachments/summary")).counts || {};
}
const ATTACHMENT_CATEGORIES = [
  ["photo", "Fotografia"],
  ["smart", "Relatório SMART"],
  ["maintenance", "Manutenção"],
  ["documentation", "Documentação"],
  ["diagnostic", "Diagnóstico"],
  ["backup", "Backup"],
  ["other", "Outro"],
];
function attachmentCategoryLabel(c) {
  return (ATTACHMENT_CATEGORIES.find((x) => x[0] === c) || ATTACHMENT_CATEGORIES.at(-1))[1];
}
function attachmentRow(a) {
  // P16: entradas cujo ficheiro está em falta ficam visíveis e podem ser apagadas, mas sem link.
  if (a.missing)
    return `<div class="attachment-row missing"><div><div class="attachment-file">EM FALTA</div></div><div class="attachment-info"><b>${esc(a.display_name || a.original_name)}</b><small><span class="attachment-category">${esc(attachmentCategoryLabel(a.category))}</span> · ${esc(a.original_name)} · ficheiro em falta: não existia no snapshot restaurado ou foi removido do disco</small></div><div class="action-row attachment-actions"><button onclick="editAttachment('${a.id}','${a.owner_type}','${a.owner_id}')">Editar</button><button class="danger" onclick="deleteAttachment('${a.id}','${a.owner_type}','${a.owner_id}')">Apagar</button></div></div>`;
  let isImage = a.mime_type.startsWith("image/");
  return `<div class="attachment-row"><div>${isImage ? `<img class="attachment-thumb" src="/api/attachments/${a.id}/download" alt="" loading="lazy" decoding="async">` : `<div class="attachment-file">${a.mime_type === "application/pdf" ? "PDF" : "FILE"}</div>`}</div><div class="attachment-info"><a href="/api/attachments/${a.id}/download" target="_blank" rel="noopener" title="${esc(a.display_name || a.original_name)}">${esc(a.display_name || a.original_name)}</a><small><span class="attachment-category">${esc(attachmentCategoryLabel(a.category))}</span> · ${esc(a.original_name)} · ${formatBytes(a.size)}</small></div><div class="action-row attachment-actions"><button onclick="editAttachment('${a.id}','${a.owner_type}','${a.owner_id}')">Editar</button><button class="danger" onclick="deleteAttachment('${a.id}','${a.owner_type}','${a.owner_id}')">Apagar</button></div></div>`;
}
function formatBytes(n) {
  n = Number(n || 0);
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(1)} GB`;
}
function attachmentCategories() {
  return ATTACHMENT_CATEGORIES;
}
function attachmentFormFields(a = {}) {
  return `<label>Nome do anexo<input id="attachmentName" maxlength="160" value="${esc(a.display_name || a.original_name || "")}" placeholder="Ex.: SMART Samsung 980 Pro — setembro 2026"></label><label>Categoria<select id="attachmentCategory">${attachmentCategories()
    .map((x) => `<option value="${x[0]}" ${a.category === x[0] ? "selected" : ""}>${x[1]}</option>`)
    .join("")}</select></label>`;
}
async function loadAttachments(type, id) {
  return (
    (await api(`/api/attachments?ownerType=${encodeURIComponent(type)}&ownerId=${encodeURIComponent(id)}`))
      .attachments || []
  );
}
function filteredAttachmentRows(data) {
  let q = ($("#attachmentSearch")?.value || "").toLowerCase().trim(),
    cat = $("#attachmentCategoryFilter")?.value || "";
  return (
    data
      .filter(
        (a) =>
          (!cat || a.category === cat) &&
          (!q ||
            `${a.display_name} ${a.original_name} ${a.category} ${a.mime_type}`.toLowerCase().includes(q)),
      )
      .map(attachmentRow)
      .join("") || '<div class="empty">Nenhum anexo corresponde aos filtros.</div>'
  );
}
async function manageAttachments(type, id) {
  let labels = { equipment: "equipamento", component: "componente", maintenance: "manutenção" };
  try {
    let data = await loadAttachments(type, id);
    modal(
      `<header><div><span class="eyebrow">ANEXOS</span><h2>Anexos do ${labels[type]}</h2><p class="muted">Organiza os ficheiros por nome e categoria.</p></div><button onclick="requestCloseModal()">×</button></header><div class="attachment-tools"><input id="attachmentSearch" data-nodirty placeholder="Pesquisar anexos..." oninput="document.querySelector('.attachment-list').innerHTML=filteredAttachmentRows(window.currentAttachments)"><select id="attachmentCategoryFilter" data-nodirty onchange="document.querySelector('.attachment-list').innerHTML=filteredAttachmentRows(window.currentAttachments)"><option value="">Todas as categorias</option>${attachmentCategories()
        .map((x) => `<option value="${x[0]}">${x[1]}</option>`)
        .join(
          "",
        )}</select></div><div class="attachment-list">${data.length ? data.map(attachmentRow).join("") : '<div class="empty">Ainda não existem anexos.</div>'}</div><form id="af" class="form attachment-upload">${attachmentFormFields()}<label>Adicionar ficheiro<input id="attachmentFile" type="file" onchange="prefillAttachmentName(this)" accept="image/*,.pdf,.txt,.json,.log,.csv,.zip,.gz,.7z,.rar,.doc,.docx,.xls,.xlsx,.smart"></label><small class="muted">Máximo 25 MB. O nome personalizado é apenas o nome apresentado; o nome original é preservado.</small>${progressBox("attachmentProgress")}</form><footer><span>${data.length} anexo${data.length === 1 ? "" : "s"}</span><button onclick="requestCloseModal()">Fechar</button><button class="accent" onclick="uploadAttachment('${type}','${id}')">Adicionar</button></footer>`,
    );
    window.currentAttachments = data;
  } catch (e) {
    alert(e.message);
  }
}
// P20: envia um ficheiro com barra de progresso (o fetch não informa o progresso do envio).
function sendWithProgress(url, file, headers, progressBox) {
  const bar = progressBox?.querySelector("progress"),
    label = progressBox?.querySelector("span");
  if (progressBox) progressBox.hidden = false;
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    Object.entries(headers).forEach(([k, v]) => xhr.setRequestHeader(k, v));
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      const pct = Math.round((e.loaded / e.total) * 100);
      if (bar) bar.value = pct;
      if (label) label.textContent = pct < 100 ? `A enviar… ${pct}%` : "A processar no servidor…";
    };
    xhr.onload = () => {
      let x = {};
      try {
        x = JSON.parse(xhr.responseText || "{}");
      } catch {}
      if (xhr.status >= 200 && xhr.status < 300) return resolve(x);
      reject(Object.assign(Error(x.error || "Pedido falhou"), { status: xhr.status, body: x }));
    };
    xhr.onerror = () => reject(Object.assign(Error("Sem ligação ao servidor."), { status: 0 }));
    xhr.send(file);
  }).finally(() => {
    if (progressBox) progressBox.hidden = true;
  });
}
function progressBox(id) {
  return `<div id="${id}" class="upload-progress" hidden><progress max="100" value="0"></progress><span>A enviar…</span></div>`;
}
// P20: o nome do anexo é preenchido com o nome do ficheiro (sem extensão), se estiver vazio.
function prefillAttachmentName(input) {
  const name = $("#attachmentName"),
    file = input.files?.[0];
  if (file && name && !name.value.trim()) name.value = file.name.replace(/\.[^.]+$/, "");
}
async function uploadAttachment(type, id) {
  let f = $("#attachmentFile")?.files?.[0],
    name = $("#attachmentName")?.value.trim(),
    category = $("#attachmentCategory")?.value || "other";
  if (!f) return fieldError("#attachmentFile", "Seleciona um ficheiro.");
  if (!name) return fieldError("#attachmentName", "Indica um nome para o anexo.");
  if (f.size > 25 * 1024 * 1024) return fieldError("#attachmentFile", "O ficheiro excede o limite de 25 MB.");
  // P15: o ficheiro vai diretamente como corpo do pedido; o browser envia-o a partir do disco.
  const q = new URLSearchParams({ ownerType: type, ownerId: id, category });
  const button = $("#drawer footer button.accent");
  if (button) button.disabled = true;
  try {
    await sendWithProgress(
      `/api/attachments?${q}`,
      f,
      {
        "Content-Type": f.type || "application/octet-stream",
        "X-File-Name": encodeURIComponent(f.name),
        "X-Display-Name": encodeURIComponent(name),
      },
      $("#attachmentProgress"),
    );
    await refreshAttachmentCounts();
    toast("Anexo adicionado");
    manageAttachments(type, id);
  } catch (e) {
    if (button?.isConnected) button.disabled = false;
    if (e.status === 401) return handleSaveError(e);
    alert(e.message);
  }
}
async function editAttachment(id, type, ownerId) {
  let a = (window.currentAttachments || []).find((x) => x.id === id);
  if (!a) return;
  modal(
    `<header><div><span class="eyebrow">ANEXO</span><h2>Editar anexo</h2></div><button onclick="manageAttachments('${type}','${ownerId}')">×</button></header><form class="form">${attachmentFormFields(a)}<small class="muted">Ficheiro original: ${esc(a.original_name)} · ${formatBytes(a.size)}</small></form><footer><button onclick="manageAttachments('${type}','${ownerId}')">Cancelar</button><button class="accent" onclick="saveAttachmentMeta('${id}','${type}','${ownerId}')">Guardar</button></footer>`,
  );
}
async function saveAttachmentMeta(id, type, ownerId) {
  let displayName = $("#attachmentName")?.value.trim(),
    category = $("#attachmentCategory")?.value || "other";
  if (!displayName) return fieldError("#attachmentName", "Indica um nome para o anexo.");
  try {
    await api(`/api/attachments/${id}`, { method: "PUT", body: JSON.stringify({ displayName, category }) });
    toast("Anexo atualizado");
    manageAttachments(type, ownerId);
  } catch (e) {
    alert(e.message);
  }
}
async function deleteAttachment(id, type, ownerId) {
  if (!confirm("Apagar este anexo?")) return;
  try {
    await api(`/api/attachments/${id}`, { method: "DELETE" });
    await refreshAttachmentCounts();
    manageAttachments(type, ownerId);
  } catch (e) {
    alert(e.message);
  }
}
function equipmentCard(e, showOrder = false) {
  showOrder = showOrder && page === "equipment";
  let last = (e.maintenance || []).slice().sort((a, b) => (b.date || "").localeCompare(a.date || ""))[0];
  let idx = state.findIndex((x) => x.id === e.id);
  return `<article class="equip-card"><div class="equip-icon">${esc((e.name || "H").slice(0, 1).toUpperCase())}</div><div class="equip-main"><div class="line"><h3>${esc(e.name)}</h3>${badge(e.status)}</div><p>${esc(e.role || "Sem função")} · ${esc(e.hostname || "sem hostname")}</p><strong>${esc(e.vendor)} ${esc(e.model)}</strong><div class="specs"><span>CPU ${esc(e.cpu || "—")}</span><span>RAM ${esc(ramSummary(e) || "—")}</span><span>Última manutenção ${esc(last?.date || "—")}</span></div></div><div class="equip-actions">${showOrder ? `<div class="order-actions"><button title="Mover para cima" aria-label="Mover ${esc(e.name)} para cima" onclick="moveEquipment('${e.id}',-1)" ${idx <= 0 ? "disabled" : ""}>↑</button><button title="Mover para baixo" aria-label="Mover ${esc(e.name)} para baixo" onclick="moveEquipment('${e.id}',1)" ${idx < 0 || idx >= state.length - 1 ? "disabled" : ""}>↓</button></div>` : ""}<div class="action-row"><button class="open" onclick="detail('${e.id}')">Abrir</button>${clip("equipment", e.id)}</div></div></article>`;
}
function dashboard() {
  let maint = state
    .flatMap((e) => (e.maintenance || []).map((m) => ({ ...m, e })))
    .sort((a, b) => (b.date || "").localeCompare(a.date || ""));
  let plans = [];
  state.forEach((e) =>
    assignedPlans(e).forEach((p) => {
      if (p.active !== false) {
        let z = planStatus(e, p);
        plans.push({ e, p, z });
      }
    }),
  );
  plans.sort((a, b) => a.z.rank - b.z.rank || (a.z.next || "9999").localeCompare(b.z.next || "9999"));
  layout(
    shell(
      "Visão geral",
      "Um resumo rápido do estado do teu homelab.",
      `<button class="accent" onclick="editEquipment()">+ Equipamento</button>`,
    ) +
      `<div class="metrics"><div><span>Equipamentos</span><b>${state.length}</b></div><div><span>Componentes</span><b>${state.reduce((n, e) => n + activeComponents(e).length, 0)}</b></div><div><span>Manutenções</span><b>${maint.length}</b></div><div><span>Última intervenção</span><b class="small">${esc(maint[0]?.date || "—")}</b><em>${esc(maint[0]?.e.name || "")}</em></div></div><section class="section"><div class="section-head"><div><h2>Manutenção preventiva</h2><p>O que merece atenção a seguir.</p></div></div>${
        plans.length
          ? `<div class="plan-grid">${plans
              .slice(0, 6)
              .map(
                (x) =>
                  `<div class="plan-card"><div class="line"><b>${esc(x.p.name)}</b><span class="badge ${x.z.cls}">${x.z.state}</span></div><p>${esc(x.e.name)}</p><div><span>Última</span> ${esc(x.z.last || "—")} · <span>Próxima</span> ${esc(x.z.next || "—")}</div></div>`,
              )
              .join("")}</div>`
          : '<div class="empty">Não existem tarefas preventivas ativas.</div>'
      }</section><section class="section"><div class="section-head"><div><h2>Equipamentos</h2><p>Inventário atual.</p></div><button onclick="go('equipment')">Ver todos →</button></div><div class="equip-grid">${state.slice(0, 6).map(equipmentCard).join("")}</div></section>`,
  );
}
function equipment() {
  layout(
    shell(
      "Equipamentos",
      "Pesquisa, organiza e abre o detalhe de qualquer máquina.",
      `<button class="accent" onclick="editEquipment()">+ Equipamento</button>`,
    ) +
      `<div class="searchbar"><input id="q" placeholder="Nome, hostname, modelo, serial, localização..." oninput="filter()"><span>${state.length} equipamentos</span></div><div class="order-hint">Use ↑ e ↓ para alterar a ordem dos equipamentos. A ordem é guardada automaticamente.</div><div id="elist" class="equip-grid">${state.map((e) => equipmentCard(e, true)).join("")}</div>`,
  );
}
function filter() {
  let q = $("#q").value.toLowerCase();
  $("#elist").innerHTML =
    state
      .filter((e) => searchText(e).includes(q))
      .map((e) => equipmentCard(e, true))
      .join("") || '<div class="empty">Nenhum equipamento encontrado.</div>';
}
async function moveEquipment(id, delta) {
  let i = state.findIndex((x) => x.id === id),
    j = i + delta;
  if (i < 0 || j < 0 || j >= state.length) return;
  if (await commit(() => ([state[i], state[j]] = [state[j], state[i]]))) render();
}
function detail(id) {
  navigate(`#/equipamentos/${encodeURIComponent(id)}`);
}
function renderDetail(id) {
  page = "equipment";
  activeId = id;
  let e = equipmentById(id);
  let ms = (e.maintenance || []).slice().sort((a, b) => (b.date || "").localeCompare(a.date || ""));
  layout(
    shell(
      e.name,
      `${e.role || "Sem função"} · ${e.vendor || ""} ${e.model || ""}`,
      `<button onclick="goBack('#/equipamentos')">← Voltar</button><button onclick="exportPdf('/api/report/equipment/${id}.pdf', this)">Exportar PDF</button><button onclick="editEquipment('${id}')">Editar</button><button class="accent" onclick="editMaintenance('${id}')">+ Manutenção</button>`,
    ) +
      `<div class="overview"><section class="section"><div class="section-head"><h2>Identificação</h2></div><div class="facts">${facts(
        [
          ["Hostname", e.hostname],
          ["Função", e.role],
          ["Fabricante", e.vendor],
          ["Modelo", e.model],
          ["Serial", e.serial],
          ["Localização", e.location],
          ["Entrada", e.created],
          ["Estado", equipmentStatusLabel(e.status)],
        ],
      )}</div></section><section class="section"><div class="section-head"><h2>Hardware</h2></div><div class="facts">${facts(
        [
          ["CPU", e.cpu],
          ["Motherboard", e.motherboard],
          ["RAM", ramSummary(e)],
          ["GPU / iGPU", e.gpu],
          ["Armazenamento", storageSummary(e)],
          ["Rede", e.nic],
          ["PSU", e.psu],
          ["BIOS", e.bios],
        ],
      )}</div></section></div>${e.notes?.trim() ? `<section class="section"><div class="section-head"><div><h2>Notas</h2></div></div><div class="equipment-notes">${esc(e.notes)}</div></section>` : ""}${componentsSection(e)}<section class="section"><div class="section-head"><div><h2>Manutenção</h2><p>${ms.length} intervenções</p></div></div>${ms.map((m) => maintenanceCard(e, m)).join("") || '<div class="empty">Sem registos.</div>'}</section>${plansSection(e)}${smartHistory(e)}`,
  );
}
function facts(a) {
  return a.map((x) => `<div><span>${esc(x[0])}</span><b>${esc(x[1] || "—")}</b></div>`).join("");
}
function smartDiskKey(d, m, index) {
  return d.deviceId ? `device:${d.deviceId}` : `legacy:${d.device || "Disco"}:${m.id || m.date || index}`;
}
function groupEquipmentSmart(e) {
  let groups = {};
  (e.maintenance || []).forEach((m) =>
    (m.results?.smartDisks || []).forEach((d, i) => {
      let k = smartDiskKey(d, m, i);
      (groups[k] ??= []).push({ date: m.date, ...d });
    }),
  );
  return Object.values(groups).map((rows) => rows.sort((a, b) => (a.date || "").localeCompare(b.date || "")));
}
function groupAllSmart() {
  let groups = {};
  state.forEach((e) =>
    (e.maintenance || []).forEach((m) =>
      (m.results?.smartDisks || []).forEach((d, i) => {
        // P22: com deviceId, o histórico é do disco, mesmo que tenha mudado de equipamento.
        let k = d.deviceId ? smartDiskKey(d, m, i) : e.id + "::" + smartDiskKey(d, m, i);
        (groups[k] ??= []).push({ e, m, d, i });
      }),
    ),
  );
  return Object.values(groups).map((rows) =>
    rows.sort((a, b) => (a.m.date || "").localeCompare(b.m.date || "")),
  );
}
// Data local do dispositivo (AAAA-MM-DD), sem passar por UTC.
// P20: mostra o erro e põe o foco no campo em causa.
function fieldError(target, message) {
  alert(message);
  const el = typeof target === "string" ? $(target) : target;
  if (el) {
    el.setAttribute("aria-invalid", "true");
    el.addEventListener("input", () => el.removeAttribute("aria-invalid"), { once: true });
    el.addEventListener("change", () => el.removeAttribute("aria-invalid"), { once: true });
    el.focus();
  }
  return false;
}
function localToday(d = new Date()) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
// P18: relatórios em PDF gerados no servidor. A data de hoje e o fuso vêm do dispositivo.
async function exportPdf(path, button) {
  const old = button?.textContent;
  if (button) {
    button.disabled = true;
    button.textContent = "A gerar PDF…";
  }
  try {
    const q = new URLSearchParams({ today: localToday(), tzOffset: String(new Date().getTimezoneOffset()) });
    const r = await fetch(`${path}?${q}`, { cache: "no-store" });
    if (!r.ok) {
      const x = await r.json().catch(() => ({}));
      throw Object.assign(Error(x.error || "Não foi possível gerar o PDF."), { status: r.status });
    }
    const blob = await r.blob();
    const m = (r.headers.get("Content-Disposition") || "").match(/filename\*=UTF-8''([^;]+)/i);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = m ? decodeURIComponent(m[1]) : "relatorio.pdf";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
    toast("PDF exportado");
  } catch (e) {
    if (e.status === 401) return handleSaveError(e);
    alert(e.status ? e.message : "Sem ligação ao servidor.");
  } finally {
    if (button && button.isConnected) {
      button.disabled = false;
      button.textContent = old;
    }
  }
}
function maintenancePreventiveLabels(e, m) {
  return (m.results?.preventivePlanIds || [])
    .map((pid) => preventivePlans.find((p) => p.id === pid)?.name)
    .filter(Boolean);
}
function maintenanceCard(e, m) {
  let r = m.results || {};
  return `<article class="maintenance-card"><div class="mc-head"><div><span>${esc(m.date)}</span><h3>${esc(m.type || "Manutenção")}</h3></div><div class="action-row">${clip("maintenance", m.id)}<button onclick="maintenanceDetail('${e.id}','${m.id}')">Abrir</button></div></div><p>${esc(m.description || "")}</p>${m.areas ? `<div class="muted">Áreas: ${esc(m.areas)}</div>` : ""}${maintenancePreventiveLabels(e, m).length ? `<div class="muted">Preventivas: ${esc(maintenancePreventiveLabels(e, m).join(" · "))}</div>` : ""}${
    m.checks
      ? `<div class="checks">${Object.entries(m.checks)
          .filter((x) => x[1])
          .map((x) => `<span>✓ ${esc(x[0])}</span>`)
          .join("")}</div>`
      : ""
  }<div class="diagnostics">${diagBlock("SMART", r.smartDisks || [], ["device", "health", "wear", "hours", "power_cycles", "data_read", "data_written", "unsafe_shutdowns", "errors", "self_test"])}${diagBlock("RAM", r.ramRows || [], ["module", "capacity", "test", "result", "errors"])}${diagBlock("Temperaturas", r.tempRows || [], ["sensor", "idle", "load", "value"])}${diagBlock("Testes", r.testRows || [], ["name", "tool", "result"])}</div>${m.notes ? `<small class="muted">${esc(m.notes)}</small>` : ""}</article>`;
}
function diagBlock(title, rows, keys) {
  if (!rows.length) return "";
  const labels = {
    device: "Disco",
    health: "Saúde",
    wear: "Desgaste",
    hours: "Horas ligado",
    power_cycles: "Ciclos de energia",
    data_read: "Dados lidos",
    data_written: "Dados escritos",
    unsafe_shutdowns: "Shutdowns inesperados",
    errors: "Erros",
    status: "Estado",
    self_test: "Último teste SMART",
    module: "Módulo",
    capacity: "Capacidade",
    test: "Teste",
    result: "Resultado",
    sensor: "Sensor",
    idle: "Idle",
    load: "Carga",
    value: "Valor",
    name: "Nome",
    tool: "Ferramenta",
  };
  return `<div class="diag"><b>${title}</b>${rows.map((r) => `<div>${keys.map((k) => (r[k] !== undefined && r[k] !== "" ? `<span><em>${labels[k] || k.charAt(0).toUpperCase() + k.slice(1)}</em>${esc(r[k])}</span>` : "")).join("")}</div>`).join("")}</div>`;
}
function assignedPlans(e) {
  return preventivePlans.filter((p) => (e.preventivePlanIds || []).includes(p.id));
}
function plansSection(e) {
  let p = assignedPlans(e);
  return `<section class="section"><div class="section-head"><div><h2>Manutenção preventiva</h2><p>Preventivas associadas a este equipamento.</p></div></div><div class="plan-grid">${
    p
      .map((x) => {
        let z = planStatus(e, x);
        return `<div class="plan-card"><div class="line"><b>${esc(x.name)}</b><span class="badge ${z.cls}">${z.state}</span></div><p>A cada ${x.intervalMonths} ${x.intervalMonths == 1 ? "mês" : "meses"}</p><div>Última: ${esc(z.last || "—")} · Próxima: ${esc(z.next || "—")}</div></div>`;
      })
      .join("") ||
    '<div class="empty">Nenhuma manutenção preventiva associada. A associação é feita em Manutenção.</div>'
  }</div></section>`;
}
// P19: soma meses a uma data AAAA-MM-DD só com o calendário (sem horas nem UTC), por isso
// não depende do fuso nem da hora de verão; se o dia não existir, fica no último do mês.
function addMonths(s, n) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s || "");
  if (!m) return "";
  const index = Number(m[2]) - 1 + Number(n);
  const year = Number(m[1]) + Math.floor(index / 12),
    month = (((index % 12) + 12) % 12) + 1;
  const day = Math.min(Number(m[3]), new Date(year, month, 0).getDate());
  return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}
function planStatus(e, p) {
  let ms = (e.maintenance || [])
    .filter((m) => (m.results?.preventivePlanIds || []).includes(p.id) && m.date)
    .sort((a, b) => b.date.localeCompare(a.date));
  let last = ms[0]?.date || "";
  let next = last ? addMonths(last, p.intervalMonths) : "";
  let today = localToday();
  if (!last) return { state: "Nunca feita", cls: "warn", rank: 1, last: "", next: "" };
  if (next < today) return { state: "Atrasada", cls: "bad", rank: 0, last, next };
  return { state: "Agendada", cls: "good", rank: 2, last, next };
}
const SMART_METRICS = [
  ["Saúde", "health"],
  ["Desgaste", "wear"],
  ["Horas", "hours"],
  ["Ciclos de energia", "power_cycles"],
  ["Dados lidos", "data_read"],
  ["Dados escritos", "data_written"],
  ["Shutdowns inesperados", "unsafe_shutdowns"],
  ["Erros", "errors"],
];
function num(x) {
  let m = String(x ?? "")
    .replace(",", ".")
    .match(/-?\d+(?:\.\d+)?/);
  return m ? Number(m[0]) : null;
}
function delta(a, b) {
  let x = num(a),
    y = num(b);
  if (x === null || y === null) return "—";
  let d = y - x;
  return (d > 0 ? "+" : "") + (Number.isInteger(d) ? d : d.toFixed(2));
}
function smartCompareHtml(previous, current) {
  return `<div class="compare">${SMART_METRICS.map(([label, key]) => `<div><span>${label}</span><b>${esc(smartValue(previous[key]))} → ${esc(smartValue(current[key]))}</b><small>${delta(previous[key], current[key])}</small></div>`).join("")}</div>`;
}
function smartValue(v) {
  return v === undefined || v === null || v === "" ? "N/A" : String(v);
}
function smartSnapshotHtml(row, date = row.date) {
  return `<div class="snapshot"><b>${esc(date || "—")}</b><span>Saúde ${esc(smartValue(row.health))}</span><span>Desgaste ${esc(smartValue(row.wear))}</span><span>Horas ${esc(smartValue(row.hours))}</span><span>Ciclos ${esc(smartValue(row.power_cycles))}</span><span>Lidas ${esc(smartValue(row.data_read))}</span><span>Escritas ${esc(smartValue(row.data_written))}</span><span>Shutdowns ${esc(smartValue(row.unsafe_shutdowns))}</span><span>Erros ${esc(smartValue(row.errors))}</span></div>`;
}
function smartHistory(e) {
  let cards = groupEquipmentSmart(e)
    .map((rows) => {
      let l = rows.at(-1),
        p = rows.at(-2),
        name = l.device || "Disco";
      return `<div class="smart-card"><div class="line"><div><span class="muted">Disco</span><h3>${esc(name)}</h3></div><span class="badge">${rows.length} medição${rows.length > 1 ? "ões" : ""}</span></div>${p ? smartCompareHtml(p, l) : '<p class="muted">É necessária uma segunda medição para comparar evolução.</p>'}<details><summary>Histórico completo</summary>${rows
        .slice()
        .reverse()
        .map((r) => smartSnapshotHtml(r))
        .join("")}</details></div>`;
    })
    .join("");
  return `<section class="section"><div class="section-head"><div><h2>Histórico SMART</h2><p>Comparação por disco, sem misturar dispositivos.</p></div></div>${cards || '<div class="empty">Ainda não existem medições SMART multi-disco.</div>'}</section>`;
}
function maintenanceDetail(eid, mid) {
  navigate(`#/manutencao/${encodeURIComponent(eid)}/${encodeURIComponent(mid)}`);
}
function renderMaintenanceDetail(eid, mid) {
  page = "maintenance";
  let e = equipmentById(eid),
    m = (e?.maintenance || []).find((x) => x.id === mid);
  if (!e || !m) return maintenance();
  let r = m.results || {},
    checks = m.checks || {};
  layout(
    shell(
      "Detalhe da manutenção",
      `${e.name} · ${m.date || "Sem data"}`,
      `<button onclick="goBack('#/manutencao')">← Voltar</button><button onclick="editMaintenance('${eid}','${mid}')">Editar</button><button class="danger" onclick="delMaintenance('${eid}','${mid}')">Apagar</button>`,
    ) +
      `<section class="section maintenance-summary"><div class="section-head"><div><span class="eyebrow">INTERVENÇÃO</span><h2>${esc(m.type || "Manutenção")}</h2><p>${esc(e.name)} · ${esc(m.date || "Sem data")}</p></div><div>${clip("maintenance", m.id)}</div></div><div class="facts">${facts(
        [
          ["Equipamento", e.name],
          ["Data", m.date],
          ["Tipo", m.type],
          ["Áreas", m.areas],
          ["Preventivas", maintenancePreventiveLabels(e, m).join(" · ")],
          ["Descrição", m.description],
        ],
      )}</div></section>${
        Object.values(checks).some(Boolean) || m.notes
          ? `<section class="section"><div class="section-head"><div><h2>Checklist e notas</h2><p>Informação registada durante a intervenção.</p></div></div>${
              Object.keys(checks).length
                ? `<div class="checks">${
                    Object.entries(checks)
                      .filter((x) => x[1])
                      .map((x) => `<span>✓ ${esc(x[0])}</span>`)
                      .join("") || "<span>Nenhum item concluído</span>"
                  }</div>`
                : ""
            }${m.notes ? `<div class="maintenance-notes">${esc(m.notes)}</div>` : ""}</section>`
          : ""
      }<section class="section"><div class="section-head"><div><h2>Diagnósticos</h2><p>Dados registados nesta intervenção.</p></div></div><div class="diagnostics">${diagBlock("SMART", r.smartDisks || [], ["device", "health", "wear", "hours", "power_cycles", "data_read", "data_written", "unsafe_shutdowns", "errors", "self_test"])}${diagBlock("RAM", r.ramRows || [], ["module", "capacity", "test", "result", "errors"])}${diagBlock("Temperaturas", r.tempRows || [], ["sensor", "idle", "load", "value"])}${diagBlock("Testes", r.testRows || [], ["name", "tool", "result"])}${!((r.smartDisks || []).length || (r.ramRows || []).length || (r.tempRows || []).length || (r.testRows || []).length) ? '<div class="empty">Não existem diagnósticos registados.</div>' : ""}</div></section>`,
  );
}
function maintenance() {
  let all = state
    .flatMap((e) => (e.maintenance || []).map((m) => ({ ...m, e })))
    .sort((a, b) => (b.date || "").localeCompare(a.date || ""));
  layout(
    shell(
      "Manutenção",
      "Histórico global de intervenções e diagnósticos.",
      `<button onclick="exportPdf('/api/report/maintenance.pdf', this)">Exportar PDF</button>`,
    ) +
      `<section class="section"><div class="section-head"><div><h2>Registos de Manutenção</h2><p>Intervenções e diagnósticos realizados nos equipamentos.</p></div></div><div class="table"><div class="tr th"><span>Data</span><span>Equipamento</span><span>Tipo</span><span>Diagnósticos</span><span>Ações</span></div>${all.map((m) => `<div class="tr"><span>${esc(m.date)}</span><b>${esc(m.e.name)}</b><span>${esc(m.type)}</span><span>${[m.results?.smartDisks?.length ? "SMART" : "", m.results?.ramRows?.length ? "RAM" : "", m.results?.tempRows?.length ? "Temperaturas" : "", m.results?.testRows?.length ? "Testes" : ""].filter(Boolean).join(" · ") || "—"}</span><span class="action-row"><button onclick="maintenanceDetail('${m.e.id}','${m.id}')">Abrir</button>${clip("maintenance", m.id)}</span></div>`).join("") || '<div class="empty">Sem manutenções.</div>'}</div></section><section class="section"><div class="section-head"><div><h2>Preventivas</h2><p>Cria cada preventiva uma única vez e associa-a aos equipamentos onde se aplica.</p></div><button class="accent" onclick="editPlan()">+ Criar preventiva</button></div><div class="plan-grid">${
        preventivePlans
          .map((p) => {
            let assigned = state.filter((e) => (e.preventivePlanIds || []).includes(p.id));
            return `<article class="plan-card"><div class="line"><b>${esc(p.name)}</b><span class="badge ${p.active !== false ? "good" : "neutral"}">${p.active !== false ? "Ativa" : "Inativa"}</span></div><p>A cada ${p.intervalMonths} ${p.intervalMonths == 1 ? "mês" : "meses"}</p><div><span class="muted">Equipamentos:</span> ${assigned.length ? esc(assigned.map((e) => e.name).join(", ")) : "Nenhum"}</div>${p.notes ? `<small class="muted">${esc(p.notes)}</small>` : ""}<div class="actions"><button onclick="editPlan('${p.id}')">Editar</button><button class="danger" onclick="delPlan('${p.id}')">Apagar</button></div></article>`;
          })
          .join("") || '<div class="empty">Ainda não existem preventivas.</div>'
      }</div></section>`,
  );
}
function smart() {
  let cards = groupAllSmart()
    .map((rows) => {
      let l = rows.at(-1),
        p = rows.at(-2),
        found = l.d.deviceId ? findComponent(l.d.deviceId) : null,
        machines = [...new Set(rows.map((x) => x.e.name))],
        where = found
          ? `${found.c.retired ? "Retirado · " : ""}em ${found.e.name}`
          : l.d.deviceId
            ? "componente apagado"
            : rows[0].e.name,
        history = machines.length > 1 ? ` · medições em ${machines.join(", ")}` : "";
      return `<article class="smart-card"><div class="line"><div><span class="muted">${esc(where + history)}</span><h2>${esc(found ? smartDeviceLabel(found.c) : l.d.device || "Disco")}</h2></div><span class="badge">${rows.length} medições</span></div>${p ? smartCompareHtml(p.d, l.d) : '<p class="muted">Aguardando uma segunda medição.</p>'}<details><summary>Ver histórico</summary>${rows
        .slice()
        .reverse()
        .map((x) => smartSnapshotHtml(x.d, x.m.date))
        .join("")}</details></article>`;
    })
    .join("");
  layout(
    shell("SMART", "Resumo de saúde, utilização, erros e testes dos discos.") +
      `<div class="section"><div class="smart-grid">${cards || '<div class="empty">Sem dados SMART.</div>'}</div></div>`,
  );
}
function backup() {
  layout(
    shell(
      "Dados & backup",
      "Cria snapshots completos e restaura o estado de uma instalação noutra instalação.",
    ) +
      `<div class="backup-grid"><section class="section"><span class="eyebrow">SNAPSHOT</span><h2>Backup completo</h2><p>Cria um ZIP com uma cópia consistente da base de dados, todos os anexos e um manifesto com o conteúdo do snapshot.</p><p class="muted">O ficheiro não inclui credenciais nem código da aplicação.</p><button class="accent" id="fullBackupBtn" onclick="downloadFullBackup()">⬇ Descarregar backup completo</button></section><section class="section"><span class="eyebrow">RESTORE</span><h2>Restaurar snapshot</h2><p>Valida um snapshot completo e substitui o estado atual desta instalação pelo estado guardado no snapshot.</p><p class="muted">Antes da substituição, o estado atual é guardado automaticamente em data/backups/ (ficam os 5 mais recentes).</p><input id="snapshotFile" type="file" accept=".zip,application/zip"><button class="danger" id="restoreBtn" onclick="restoreSnapshot()">⬆ Restaurar snapshot</button>${progressBox("restoreProgress")}</section><section class="section"><h2>Exportar dados</h2><p>Cria um JSON com equipamento, componentes, planos e manutenção, para leitura e portabilidade. Não inclui anexos e não pode ser importado: para restaurar, usa um snapshot.</p><button onclick="exportData()">Exportar JSON</button></section></div>`,
  );
}
async function downloadFullBackup() {
  const b = $("#fullBackupBtn");
  if (!b) return;
  const old = b.textContent;
  b.disabled = true;
  b.textContent = "A criar backup…";
  try {
    // P15: o servidor prepara o ZIP; o browser descarrega-o depois diretamente para o disco,
    // sem o carregar para memória (importante no telemóvel).
    const r = await api("/api/backup/prepare", { method: "POST" });
    const missing = r.missing_attachments || [];
    if (missing.length)
      alert(
        `O backup foi criado, mas ${missing.length} anexo(s) não têm ficheiro no disco e ficam só registados no manifesto:\n` +
          missing
            .slice(0, 10)
            .map((x) => "• " + (x.display_name || x.original_name || x.id))
            .join("\n") +
          (missing.length > 10 ? `\n… e mais ${missing.length - 10}` : ""),
      );
    const a = document.createElement("a");
    a.href = `/api/backup/download/${encodeURIComponent(r.token)}`;
    a.download = "";
    document.body.append(a);
    a.click();
    a.remove();
    toast("Backup completo criado");
  } catch (e) {
    alert(e.message);
  } finally {
    b.disabled = false;
    b.textContent = old;
  }
}
function restoreSummary(s) {
  return (
    "Data: " +
    (s.created_at || s.manifest?.created_at || "desconhecida") +
    "\nEquipamentos: " +
    s.equipment_count +
    "\nComponentes: " +
    s.component_count +
    "\nManutenções: " +
    s.maintenance_count +
    "\nPreventivas: " +
    s.preventive_plan_count +
    "\nAnexos: " +
    s.attachment_count +
    (s.missing_attachment_count ? `\nAnexos em falta no snapshot: ${s.missing_attachment_count}` : "")
  );
}
async function restoreSnapshot() {
  const input = $("#snapshotFile"),
    b = $("#restoreBtn"),
    f = input?.files?.[0];
  if (!f) return fieldError(input, "Seleciona um snapshot ZIP.");
  if (!f.name.toLowerCase().endsWith(".zip"))
    return fieldError(input, "Seleciona um ficheiro ZIP de snapshot.");
  if (f.size > 512 * 1024 * 1024) return fieldError(input, "O snapshot excede o limite de 512 MB.");
  const old = b.textContent;
  b.disabled = true;
  b.textContent = "A validar…";
  try {
    // P15: o ZIP é enviado uma só vez; o servidor valida-o e guarda-o à espera de confirmação.
    b.textContent = "A enviar…";
    const v = await sendWithProgress(
      "/api/restore/upload",
      f,
      { "Content-Type": "application/zip" },
      $("#restoreProgress"),
    );
    const s = v.summary;
    const details = restoreSummary(s);
    if (
      !confirm(
        `SNAPSHOT VÁLIDO\n\n${details}\n\nATENÇÃO: esta operação vai substituir os dados atuais desta instalação pelo snapshot. Antes da substituição, o estado atual será guardado automaticamente em data/backups/.\n\nContinuar?`,
      )
    )
      return;
    b.textContent = "A restaurar…";
    const r = await api("/api/restore/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: v.token, confirm: true }),
    });
    const fresh = await api("/api/data");
    state = fresh.data.equipment || [];
    preventivePlans = fresh.data.preventivePlans || [];
    revision = fresh.revision;
    await refreshAttachmentCounts();
    const missing = r.summary?.missing_attachments || [];
    alert(
      `Snapshot restaurado com sucesso.\n\nO estado anterior ficou guardado em data/backups/${r.summary?.safety_backup || ""}.` +
        (missing.length
          ? `\n\nAnexos em falta no snapshot (${missing.length}); as entradas ficam marcadas como “ficheiro em falta”:\n` +
            missing
              .slice(0, 10)
              .map((x) => "• " + (x.display_name || x.original_name || x.id))
              .join("\n") +
            (missing.length > 10 ? `\n… e mais ${missing.length - 10}` : "")
          : ""),
    );
    render();
  } catch (e) {
    alert(e.message);
  } finally {
    b.disabled = false;
    b.textContent = old;
  }
}
function exportData() {
  let b = new Blob(
      [
        JSON.stringify(
          { version: 2, exportedAt: new Date().toISOString(), data: { equipment: state, preventivePlans } },
          null,
          2,
        ),
      ],
      { type: "application/json" },
    ),
    a = document.createElement("a");
  a.href = URL.createObjectURL(b);
  a.download = "homelab-register-backup.json";
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 500);
}
let modalPreviousFocus = null,
  modalBaseline = null;
function modal(html) {
  // P19: o modal ocupa uma entrada do histórico, para o «voltar» o fechar primeiro.
  if (!document.body.classList.contains("modal-open") && !modalHistory && appReady) {
    history.pushState({ n: historyDepth() + 1, modal: true }, "");
    modalHistory = true;
  }
  modalPreviousFocus = document.activeElement;
  $("#drawer").innerHTML =
    `<div class="shade" onclick="requestCloseModal()"></div><section class="modal" role="dialog" aria-modal="true">${html}</section>`;
  document.body.classList.add("modal-open");
  // P20: estado inicial do formulário (depois de desenhadas as linhas dinâmicas).
  modalBaseline = null;
  requestAnimationFrame(() => (modalBaseline = modalSnapshot()));
  let m = $("#drawer .modal");
  let f = m.querySelector("input,select,textarea,button");
  if (f) requestAnimationFrame(() => f.focus());
}
function closeModal(options = {}) {
  if (modalHistory && !options.fromHistory) {
    modalHistory = false;
    ignorePop = true;
    history.back();
  }
  document.body.classList.remove("modal-open");
  $("#drawer").innerHTML = "";
  modalBaseline = null;
  // P20: o foco volta ao elemento que abriu o modal ou, se já não existir, ao título da página.
  const back = modalPreviousFocus;
  modalPreviousFocus = null;
  requestAnimationFrame(() => {
    const target = back?.isConnected ? back : document.querySelector("main h1");
    if (target && !back?.isConnected) target.setAttribute("tabindex", "-1");
    target?.focus?.();
  });
}
// P20: valores dos campos do modal, para saber se há alterações por guardar.
function modalSnapshot() {
  return JSON.stringify(
    [...document.querySelectorAll("#drawer .modal :is(input, select, textarea):not([data-nodirty])")].map(
      (x) =>
        x.type === "checkbox" || x.type === "radio"
          ? x.checked
          : x.type === "file"
            ? x.files.length
            : x.value,
    ),
  );
}
function modalDirty() {
  return (
    modalBaseline !== null &&
    document.body.classList.contains("modal-open") &&
    modalSnapshot() !== modalBaseline
  );
}
// Fecho pedido pelo utilizador (×, Cancelar, clique fora, Esc, «voltar»): pergunta se há alterações.
function requestCloseModal() {
  if (modalDirty() && !confirm("Tens alterações por guardar.\n\nDescartar as alterações?")) return false;
  closeModal();
  return true;
}
// U08 (P27): os formulários dos modais nunca são submetidos pelo browser. Um Enter num
// formulário com um só campo de texto (anexos) recarregava a página e perdia o modal.
document.addEventListener("submit", (e) => {
  if (e.target.closest("#drawer")) e.preventDefault();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && document.body.classList.contains("modal-open")) {
    e.preventDefault();
    requestCloseModal();
  }
});
function inputs(obj, fields) {
  return fields
    .map(
      ([k, l, t = "text"]) =>
        `<label>${l}${t === "textarea" ? `<textarea name="${k}">${esc(obj[k] || "")}</textarea>` : `<input name="${k}" type="${t}" value="${esc(obj[k] ?? "")}">`}</label>`,
    )
    .join("");
}
function formVal(id) {
  let f = new FormData($(id));
  return Object.fromEntries(f.entries());
}
function editEquipment(id) {
  let e = id
    ? equipmentById(id)
    : {
        id: "eq-" + Date.now(),
        name: "",
        hostname: "",
        role: "",
        vendor: "",
        model: "",
        serial: "",
        location: "",
        created: localToday(),
        status: "ok",
        cpu: "",
        motherboard: "",
        gpu: "",
        nic: "",
        psu: "",
        bios: "",
        notes: "",
        components: [],
        preventivePlanIds: [],
        maintenance: [],
      };
  modal(
    `<header><div><span class="eyebrow">${id ? "EDITAR" : "NOVO"}</span><h2>${id ? "Editar equipamento" : "Novo equipamento"}</h2></div><button onclick="requestCloseModal()">×</button></header><form id="ef" class="form"><div class="formgrid">${inputs(
      e,
      [
        ["name", "Nome"],
        ["hostname", "Hostname"],
        ["role", "Função"],
        ["vendor", "Fabricante"],
        ["model", "Modelo"],
        ["serial", "Serial"],
        ["location", "Localização"],
        ["created", "Entrada", "date"],
        ["cpu", "CPU"],
        ["motherboard", "Motherboard"],
        ["gpu", "GPU / iGPU"],
        ["nic", "Rede"],
        ["psu", "PSU"],
        ["bios", "BIOS"],
        ["notes", "Notas", "textarea"],
      ],
    )}</div><p class="muted">RAM e armazenamento são calculados a partir dos componentes ativos.</p><label>Estado<select name="status"><option value="ok" ${e.status === "ok" ? "selected" : ""}>Operacional</option><option value="warn" ${e.status === "warn" ? "selected" : ""}>Atenção</option><option value="offline" ${e.status === "offline" ? "selected" : ""}>Offline</option></select></label></form><footer>${id ? `<button class="danger" onclick="deleteEquipment('${id}')">Apagar equipamento</button>` : ""}<span></span><button onclick="requestCloseModal()">Cancelar</button><button class="accent" onclick="saveEquipment('${id || ""}')">Guardar</button></footer></section>`,
  );
}
async function saveEquipment(id) {
  event.preventDefault();
  let v = formVal("#ef");
  if (!v.name.trim()) return fieldError("#ef [name=name]", "Indica um nome.");
  const ok = await commit(() => {
    if (id) Object.assign(equipmentById(id), v);
    else {
      v.id = "eq-" + Date.now();
      v.components = [];
      v.preventivePlanIds = [];
      v.maintenance = [];
      state.push(v);
    }
  });
  if (!ok) return;
  closeModal();
  render();
}
// B15 (P28): a confirmação diz o que mais se perde: anexos (do equipamento, dos seus
// componentes e manutenções) e medições SMART/RAM feitas aqui a componentes que já
// foram transferidos para outro equipamento.
function deleteEquipmentMessage(e) {
  const attachments =
    attCount("equipment", e.id) +
    (e.components || []).reduce((n, c) => n + attCount("component", c.id), 0) +
    (e.maintenance || []).reduce((n, m) => n + attCount("maintenance", m.id), 0);
  const elsewhere = new Map();
  (e.maintenance || []).forEach((m) =>
    [
      ...(m.results?.smartDisks || []).map((d) => d.deviceId),
      ...(m.results?.ramRows || []).map((r) => r.ramComponentId),
    ].forEach((cid) => {
      const found = cid ? findComponent(cid) : null;
      if (found && found.e.id !== e.id) elsewhere.set(cid, found);
    }),
  );
  let message = `Apagar «${e.name}»? Esta ação remove também componentes, associações e histórico.`;
  if (attachments)
    message +=
      attachments === 1
        ? "\n\n1 anexo também será apagado definitivamente."
        : `\n\n${attachments} anexos também serão apagados definitivamente.`;
  if (elsewhere.size)
    message +=
      "\n\nAs medições SMART/RAM registadas aqui também se perdem para estes componentes, que já estão noutro equipamento:\n" +
      [...elsewhere.values()].map((x) => `• ${x.c.name} (agora em ${x.e.name})`).join("\n");
  return message;
}
async function deleteEquipment(id) {
  // P22: apagar o equipamento apagaria estes componentes e partiria o histórico dos outros.
  let blocked = (equipmentById(id).components || []).filter((c) =>
    componentHistoryRefs(c.id).some((r) => r.e.id !== id),
  );
  if (blocked.length)
    return alert(
      `Não é possível apagar «${equipmentById(id).name}»: ${blocked.length === 1 ? "o componente" : "os componentes"} ${blocked.map((c) => `«${c.name}»`).join(", ")} ${blocked.length === 1 ? "tem" : "têm"} histórico noutros equipamentos.\n\nTransfere-${blocked.length === 1 ? "o" : "os"} primeiro para outro equipamento, para o histórico ficar preservado.`,
    );
  if (!confirm(deleteEquipmentMessage(equipmentById(id)))) return;
  if (!(await commit(() => (state = state.filter((x) => x.id !== id))))) return;
  closeModal();
  go("equipment");
}
function editComponent(eid, cid) {
  let e = equipmentById(eid),
    raw = cid
      ? e.components.find((x) => x.id === cid)
      : {
          id: "c-" + Date.now(),
          name: "",
          type: "other",
          model: "",
          serial: "",
          capacity: "",
          technology: "",
          status: "",
        };
  let legacyStatus = raw.status || raw.health || "";
  let c = { ...raw, status: { Excelente: "ok", Saudável: "ok" }[legacyStatus] || legacyStatus };
  let cat = componentCategories
    .map(([k, l]) => `<option value="${k}" ${c.type === k ? "selected" : ""}>${l}</option>`)
    .join("");
  modal(
    `<header><h2>${cid ? "Editar componente" : "Novo componente"}</h2><button onclick="requestCloseModal()">×</button></header><form id="cf" class="form"><div class="formgrid">${inputs(
      c,
      [
        ["name", "Nome"],
        ["model", "Modelo"],
        ["serial", "Serial"],
        ["capacity", "Capacidade"],
      ],
    )}</div><label>Estado<select name="status"><option value="" ${!c.status ? "selected" : ""}>Não definido</option><option value="ok" ${c.status === "ok" ? "selected" : ""}>Operacional</option><option value="warn" ${c.status === "warn" ? "selected" : ""}>Atenção</option><option value="offline" ${c.status === "offline" ? "selected" : ""}>Offline</option></select></label><label>Categoria<select name="type" onchange="updateTechnologyOptions(this.value)">${cat}</select></label><label>Tecnologia<input name="technology" list="technology-options" value="${esc(c.technology || "")}" placeholder="p. ex. DDR4, NVMe" autocomplete="off"></label><datalist id="technology-options">${technologySuggestions(
      c.type,
    )
      .map((t) => `<option value="${esc(t)}"></option>`)
      .join(
        "",
      )}</datalist></form><footer><span></span><button onclick="requestCloseModal()">Cancelar</button><button class="accent" onclick="saveComponent('${eid}','${cid || ""}','${c.id}')">Guardar</button></footer>`,
  );
}
async function saveComponent(eid, cid, tmp) {
  event.preventDefault();
  let e = equipmentById(eid),
    v = formVal("#cf");
  if (!v.name.trim()) return fieldError("#cf [name=name]", "Indica um nome.");
  v.type = componentCategories.some((x) => x[0] === v.type) ? v.type : "other";
  const ok = await commit(() => {
    if (cid) {
      let target = e.components.find((x) => x.id === cid);
      Object.assign(target, { ...v, health: v.status || "" });
      delete target.status;
    } else {
      v.id = tmp;
      v.health = v.status || "";
      delete v.status;
      e.components.push(v);
    }
  });
  if (!ok) return;
  closeModal();
  detail(eid);
}
async function moveComponent(eid, cid, dir) {
  let e = equipmentById(eid),
    active = activeComponents(e),
    k = active.findIndex((c) => c.id === cid),
    other = active[k + dir];
  if (k < 0 || !other) return;
  let i = e.components.indexOf(active[k]),
    j = e.components.indexOf(other);
  if (!(await commit(() => ([e.components[i], e.components[j]] = [e.components[j], e.components[i]]))))
    return;
  detail(eid);
}
// P22: retirar mantém o componente, o histórico SMART e os anexos; só deixa de estar ativo.
async function retireComponent(eid, cid) {
  let c = equipmentById(eid).components.find((x) => x.id === cid);
  if (
    !confirm(
      `Retirar «${c.name}»?\n\nDeixa de estar ativo: não conta no hardware do equipamento e não aparece em novas manutenções. O componente, o histórico SMART e os anexos ficam preservados, e podes reativá-lo a qualquer momento.`,
    )
  )
    return;
  if (await commit(() => (c.retired = true))) detail(eid);
}
async function reactivateComponent(eid, cid) {
  let c = equipmentById(eid).components.find((x) => x.id === cid);
  if (await commit(() => (c.retired = false))) detail(eid);
}
// P22: transferir mantém o mesmo componente (ID, dados, anexos, histórico) e regista a origem.
function transferComponent(eid, cid) {
  let e = equipmentById(eid),
    c = e.components.find((x) => x.id === cid),
    targets = state.filter((x) => x.id !== eid);
  if (!targets.length) return alert("Não existe outro equipamento para onde transferir.");
  modal(
    `<header><h2>Transferir componente</h2><button onclick="requestCloseModal()">×</button></header><form id="tf" class="form" onsubmit="event.preventDefault()"><p>«${esc(c.name)}» sai de <b>${esc(e.name)}</b> e passa para o equipamento escolhido. Mantém a identidade, os dados, os anexos e o histórico SMART, e fica registado como «movido de ${esc(e.name)}».</p><label>Equipamento de destino<select name="target">${targets.map((x) => `<option value="${esc(x.id)}">${esc(x.name)}</option>`).join("")}</select></label></form><footer><span></span><button onclick="requestCloseModal()">Cancelar</button><button class="accent" onclick="saveTransfer('${eid}','${cid}')">Transferir</button></footer>`,
  );
}
async function saveTransfer(eid, cid) {
  let targetId = $("#tf [name=target]").value;
  let ok = await commit(() => {
    let source = equipmentById(eid),
      target = equipmentById(targetId),
      c = source.components.find((x) => x.id === cid);
    source.components = source.components.filter((x) => x.id !== cid);
    c.previousEquipmentIds = [...(c.previousEquipmentIds || []), eid];
    (target.components ??= []).push(c);
  });
  if (!ok) return;
  closeModal();
  toast(`Transferido para ${equipmentById(targetId).name}`);
  detail(eid);
}
async function delComponent(eid, cid) {
  let c = equipmentById(eid).components.find((x) => x.id === cid);
  // Proteção intencional: com histórico (em qualquer equipamento), só se pode retirar.
  if (componentHistoryRefs(cid).length)
    return alert(
      `«${c.name}» tem registos de diagnóstico (SMART ou RAM) e não pode ser apagado, para preservar o histórico.\n\nSe deixou de estar em uso, usa «Retirar»: o componente, o histórico e os anexos ficam guardados.`,
    );
  let n = attCount("component", cid);
  let warning = !n
    ? ""
    : n === 1
      ? "\n\nO anexo deste componente também será apagado definitivamente."
      : `\n\nOs ${n} anexos deste componente também serão apagados definitivamente.`;
  if (!confirm(`Apagar «${c.name}» definitivamente?${warning}`)) return;
  const ok = await commit(() => {
    let e = equipmentById(eid);
    e.components = e.components.filter((x) => x.id !== cid);
  });
  if (!ok) return;
  await refreshAttachmentCounts();
  detail(eid);
}
function editPlan(pid) {
  let p = pid
    ? preventivePlans.find((x) => x.id === pid)
    : { id: "p-" + Date.now(), name: "", intervalMonths: 6, active: true, notes: "" };
  let assigned = new Set(state.filter((e) => (e.preventivePlanIds || []).includes(p.id)).map((e) => e.id));
  modal(
    `<header><div><span class="eyebrow">${pid ? "EDITAR" : "NOVA"}</span><h2>${pid ? "Editar preventiva" : "Nova preventiva"}</h2></div><button onclick="requestCloseModal()">×</button></header><form id="pf" class="form"><div class="formgrid">${inputs(
      p,
      [
        ["name", "Nome"],
        ["intervalMonths", "Intervalo em meses", "number"],
        ["notes", "Notas", "textarea"],
      ],
    )}</div><label class="check"><input name="active" type="checkbox" ${p.active !== false ? "checked" : ""}> Ativa</label><div class="section-head compact"><div><h3>Equipamentos abrangidos</h3><p>Seleciona os dispositivos onde esta preventiva se aplica.</p></div></div><div class="assign-grid">${state.map((e) => `<label class="check"><input type="checkbox" name="eq-${e.id}" ${assigned.has(e.id) ? "checked" : ""}> ${esc(e.name)}</label>`).join("") || '<div class="empty">Ainda não existem equipamentos.</div>'}</div></form><footer>${pid ? `<button class="danger" onclick="delPlan('${pid}')">Apagar</button>` : ""}<span></span><button onclick="requestCloseModal()">Cancelar</button><button class="accent" onclick="savePlan('${pid || ""}','${p.id}')">Guardar</button></footer>`,
  );
}
async function savePlan(pid, tmp) {
  event.preventDefault();
  let p = pid ? preventivePlans.find((x) => x.id === pid) : { id: tmp };
  let v = formVal("#pf");
  if (!v.name.trim()) return fieldError("#pf [name=name]", "Indica um nome.");
  v.intervalMonths = Math.max(1, Math.min(120, Number(v.intervalMonths || 6)));
  v.active = $("#pf [name=active]").checked;
  const ok = await commit(() => {
    if (pid) p = preventivePlans.find((x) => x.id === pid);
    p.name = v.name;
    p.intervalMonths = v.intervalMonths;
    p.active = v.active;
    p.notes = v.notes || "";
    if (!pid) preventivePlans.push(p);
    state.forEach((e) => {
      e.preventivePlanIds = e.preventivePlanIds || [];
      let checked = $(`[name="eq-${e.id}"]`)?.checked;
      if (checked && !e.preventivePlanIds.includes(p.id)) e.preventivePlanIds.push(p.id);
      if (!checked) e.preventivePlanIds = e.preventivePlanIds.filter((x) => x !== p.id);
    });
  });
  if (!ok) return;
  closeModal();
  maintenance();
}
async function delPlan(pid) {
  let p = preventivePlans.find((x) => x.id === pid);
  if (!p) return;
  let used = state.some((e) =>
    (e.maintenance || []).some((m) => (m.results?.preventivePlanIds || []).includes(pid)),
  );
  if (used)
    return alert(
      `A preventiva «${p.name}» já foi utilizada numa manutenção e não pode ser apagada. Desativa-a em vez de a apagar para preservar o histórico.`,
    );
  if (confirm(`Apagar «${p.name}»?`)) {
    const ok = await commit(() => {
      preventivePlans = preventivePlans.filter((x) => x.id !== pid);
      state.forEach((e) => {
        e.preventivePlanIds = (e.preventivePlanIds || []).filter((x) => x !== pid);
      });
    });
    if (!ok) return;
    closeModal();
    maintenance();
  }
}
let maintenanceEquipmentId = null;
// P22: componentes ativos (os retirados não entram em novas manutenções nem no hardware).
function activeComponents(e) {
  return (e?.components || []).filter((c) => !c.retired);
}
function storageComponents(e) {
  return activeComponents(e).filter((c) => c.type === "storage");
}
function ramComponents(e) {
  return activeComponents(e).filter((c) => c.type === "ram");
}
// P23: RAM e armazenamento do equipamento são calculados a partir dos componentes ativos
// (retirados não contam; transferidos contam só no equipamento onde estão).
function parseCapacityGB(text, base) {
  const m = String(text || "")
    .trim()
    .match(/^(\d+(?:[.,]\d+)?)\s*([tgm])i?b?$/i);
  if (!m) return null;
  const n = parseFloat(m[1].replace(",", ".")),
    unit = m[2].toLowerCase();
  return unit === "t" ? n * base : unit === "m" ? n / base : n;
}
function formatNumber(n) {
  return String(Math.round(n * 100) / 100).replace(".", ",");
}
function formatCapacity(gb, base) {
  return gb >= base ? `${formatNumber(gb / base)} TB` : `${formatNumber(gb)} GB`;
}
function technologiesOf(list) {
  return [...new Set(list.map((c) => String(c.technology || "").trim()).filter(Boolean))].join("/");
}
function capacityTotal(list, base) {
  const sizes = list.map((c) => parseCapacityGB(c.capacity, base)),
    known = sizes.filter((x) => x !== null);
  return { sizes, known, partial: known.length < sizes.length, total: known.reduce((a, b) => a + b, 0) };
}
function ramSummary(e) {
  const modules = ramComponents(e);
  if (!modules.length) return "";
  const { sizes, known, partial, total } = capacityTotal(modules, 1024);
  const head = known.length
    ? `${partial ? "≥ " : ""}${formatCapacity(total, 1024)}`
    : "Capacidade desconhecida";
  const breakdown =
    !partial && known.every((x) => x === known[0])
      ? `${modules.length} × ${formatCapacity(known[0], 1024)}`
      : `${modules.length} módulos: ${sizes.map((x) => (x === null ? "?" : formatNumber(x))).join(" + ")} GB`;
  const tech = technologiesOf(modules);
  return `${head}${tech ? ` ${tech}` : ""} (${breakdown})${partial ? " — capacidade ilegível em algum módulo" : ""}`;
}
function storageSummary(e) {
  const devices = storageComponents(e);
  if (!devices.length) return "";
  const { known, partial, total } = capacityTotal(devices, 1000);
  const head = known.length
    ? `${partial ? "≥ " : ""}${formatCapacity(total, 1000)}`
    : "Capacidade desconhecida";
  const tech = technologiesOf(devices);
  const detail =
    devices.length === 1
      ? tech
        ? ` ${tech}`
        : ""
      : ` (${[`${devices.length} dispositivos`, tech].filter(Boolean).join(" · ")})`;
  return `${head}${detail}${partial ? " — capacidade ilegível em algum dispositivo" : ""}`;
}
// P23: sugestões para o campo «Tecnologia», conforme a categoria (texto livre).
function technologySuggestions(type) {
  return (
    {
      ram: ["DDR3", "DDR4", "DDR5", "LPDDR4X", "LPDDR5"],
      storage: ["NVMe", "SATA SSD", "HDD", "SAS", "eMMC"],
    }[type] || []
  );
}
function updateTechnologyOptions(type) {
  const list = $("#technology-options");
  if (list)
    list.innerHTML = technologySuggestions(type)
      .map((t) => `<option value="${esc(t)}"></option>`)
      .join("");
}
// P22: procura um componente em qualquer equipamento (pode ter sido transferido).
function findComponent(cid) {
  for (const e of state) {
    const c = (e.components || []).find((x) => x.id === cid);
    if (c) return { e, c };
  }
  return null;
}
// P22: registos de manutenção (em qualquer equipamento) com SMART ou RAM deste componente.
function componentHistoryRefs(cid) {
  return state.flatMap((e) =>
    (e.maintenance || [])
      .filter(
        (m) =>
          (m.results?.smartDisks || []).some((d) => d.deviceId === cid) ||
          (m.results?.ramRows || []).some((r) => r.ramComponentId === cid),
      )
      .map((m) => ({ e, m })),
  );
}
function movedFromLabel(c) {
  const previous = (c.previousEquipmentIds || []).at(-1);
  if (!previous) return "";
  return equipmentById(previous)?.name || "equipamento apagado";
}
function componentWhereSuffix(found, e) {
  if (!found) return "";
  const parts = [];
  if (found.c.retired) parts.push("retirado");
  if (found.e.id !== e?.id) parts.push(`agora em ${found.e.name}`);
  return parts.length ? ` (${parts.join(", ")})` : "";
}
function componentRow(e, c, i, active) {
  const moved = movedFromLabel(c);
  const meta = [categoryLabel(c.type), c.model, c.capacity, c.technology].filter(Boolean).join(" · ");
  const status = [c.retired ? "Retirado" : "", componentStatusLabel(c.status || c.health)]
    .filter(Boolean)
    .join(" · ");
  const count = activeComponents(e).length;
  const order = active
    ? `<div class="order-actions"><button title="Mover para cima" aria-label="Mover ${esc(c.name)} para cima" onclick="moveComponent('${e.id}','${c.id}',-1)" ${i <= 0 ? "disabled" : ""}>↑</button><button title="Mover para baixo" aria-label="Mover ${esc(c.name)} para baixo" onclick="moveComponent('${e.id}','${c.id}',1)" ${i >= count - 1 ? "disabled" : ""}>↓</button></div>`
    : "";
  const lifecycle = c.retired
    ? `<button onclick="reactivateComponent('${e.id}','${c.id}')">Reativar</button>`
    : `<button onclick="retireComponent('${e.id}','${c.id}')">Retirar</button>`;
  return `<div class="component-row${c.retired ? " retired" : ""}"><div><b>${esc(c.name)}</b><span>${esc(meta)}</span><small>SN ${esc(c.serial || "—")} · ${esc(status)}${moved ? ` · movido de ${esc(moved)}` : ""}</small></div><div class="component-row-actions">${order}<div class="action-row">${clip("component", c.id)}<button onclick="editComponent('${e.id}','${c.id}')">Editar</button><button onclick="transferComponent('${e.id}','${c.id}')">Transferir</button>${lifecycle}<button class="danger" onclick="delComponent('${e.id}','${c.id}')">Apagar</button></div></div></div>`;
}
function componentsSection(e) {
  const active = activeComponents(e),
    retired = (e.components || []).filter((c) => c.retired);
  return `<section class="section"><div class="section-head"><div><h2>Componentes</h2><p>${active.length} ativo${active.length === 1 ? "" : "s"}${retired.length ? ` · ${retired.length} retirado${retired.length === 1 ? "" : "s"}` : ""}</p></div><button onclick="editComponent('${e.id}')">+ Componente</button></div>${active.length > 1 ? '<div class="order-hint">Use ↑ e ↓ para alterar a ordem dos componentes. A ordem é guardada automaticamente.</div>' : ""}<div class="component-list">${active.map((c, i) => componentRow(e, c, i, true)).join("") || '<div class="empty">Sem componentes ativos.</div>'}</div>${retired.length ? `<h3 class="subhead">Retirados</h3><p class="muted">Fora de uso: não contam no hardware do equipamento nem aparecem em novas manutenções. O histórico SMART e os anexos ficam preservados.</p><div class="component-list">${retired.map((c, i) => componentRow(e, c, i, false)).join("")}</div>` : ""}</section>`;
}
function smartDeviceLabel(c) {
  return [c.name, c.model, c.capacity].filter(Boolean).join(" · ") || c.serial || "Disco";
}
function ramComponentLabel(c) {
  return [c.name, c.model, c.capacity].filter(Boolean).join(" · ") || c.serial || "Módulo RAM";
}
// B10 (P26): associa um registo antigo (só com o nome do disco/módulo) a um componente.
// Primeiro por igualdade, depois por inclusão; campos vazios nunca contam
// (antes, «texto».includes("") era sempre verdadeiro e escolhia o componente errado).
function findLegacyComponent(list, text, valuesOf) {
  const q = String(text || "")
    .trim()
    .toLowerCase();
  if (!q) return null;
  const values = (c) =>
    valuesOf(c)
      .map((v) =>
        String(v || "")
          .trim()
          .toLowerCase(),
      )
      .filter(Boolean);
  return (
    list.find((c) => values(c).some((v) => v === q)) ||
    list.find((c) => values(c).some((v) => v.includes(q) || q.includes(v))) ||
    null
  );
}
function resolveSmartRows(e, items) {
  return (items || []).map((r) => {
    let x = { ...r };
    if (!x.deviceId && x.device) {
      let c = findLegacyComponent(storageComponents(e), x.device, (c) => [
        c.name,
        c.model,
        c.serial,
        smartDeviceLabel(c),
      ]);
      if (c) x.deviceId = c.id;
    }
    let c = x.deviceId ? findComponent(x.deviceId)?.c : null;
    if (c) x.device = smartDeviceLabel(c);
    return x;
  });
}
function resolveRamRows(e, items) {
  return (items || []).map((r) => {
    let x = { ...r };
    if (!x.ramComponentId && x.module) {
      let c = findLegacyComponent(ramComponents(e), x.module, (c) => [
        c.name,
        c.model,
        c.serial,
        c.capacity,
        ramComponentLabel(c),
      ]);
      if (c) x.ramComponentId = c.id;
    }
    let c = x.ramComponentId ? findComponent(x.ramComponentId)?.c : null;
    if (c) {
      x.module = ramComponentLabel(c);
      x.capacity = c.capacity || x.capacity || "";
    }
    return x;
  });
}
// P22: um registo antigo pode referir um componente retirado ou já transferido; mantém-se
// selecionado (com a indicação) para a gravação não perder a referência.
function componentOptions(list, currentId, labelOf, emptyText, placeholder) {
  let e = equipmentById(maintenanceEquipmentId);
  let extra = currentId && !list.some((c) => c.id === currentId) ? findComponent(currentId) : null;
  if (!list.length && !extra) return `<option value="">${emptyText}</option>`;
  let extraOption = extra
    ? `<option value="${esc(extra.c.id)}" selected>${esc(labelOf(extra.c) + componentWhereSuffix(extra, e))}</option>`
    : "";
  return `<option value="">${placeholder}</option>${extraOption}${list.map((c) => `<option value="${esc(c.id)}" ${currentId === c.id ? "selected" : ""}>${esc(labelOf(c))}</option>`).join("")}`;
}
function smartOptions(r) {
  let e = equipmentById(maintenanceEquipmentId);
  return componentOptions(
    storageComponents(e),
    r.deviceId,
    smartDeviceLabel,
    "Nenhum componente de armazenamento",
    "Selecionar disco…",
  );
}
function ramOptions(r) {
  let e = equipmentById(maintenanceEquipmentId);
  return componentOptions(
    ramComponents(e),
    r.ramComponentId,
    ramComponentLabel,
    "Nenhum componente RAM",
    "Selecionar módulo RAM…",
  );
}
let rows = { smart: [], ram: [], temp: [], test: [] };
function rowHtml(kind, r, i) {
  let defs = {
    ram: [
      ["module", "Módulo"],
      ["capacity", "Capacidade"],
      ["test", "Teste"],
      ["result", "Resultado"],
      ["errors", "Erros"],
      ["notes", "Notas", "textarea"],
    ],
    temp: [
      ["sensor", "Sensor"],
      ["idle", "Idle"],
      ["load", "Carga"],
      ["value", "Temperatura"],
      ["notes", "Notas", "textarea"],
    ],
    test: [
      ["name", "Teste"],
      ["tool", "Ferramenta"],
      ["result", "Resultado", "textarea"],
      ["notes", "Notas", "textarea"],
    ],
  }[kind];
  let fields =
    kind === "smart"
      ? `<label>Disco<select data-k="deviceId" data-i="${i}" data-r="smart">${smartOptions(r)}</select></label><div class="formgrid">${[
          ["health", "Saúde"],
          ["wear", "Desgaste"],
          ["hours", "Horas ligado"],
          ["power_cycles", "Ciclos de energia"],
          ["data_read", "Dados lidos"],
          ["data_written", "Dados escritos"],
          ["unsafe_shutdowns", "Shutdowns inesperados"],
          ["errors", "Erros"],
          ["self_test", "Último teste SMART"],
        ]
          .map(
            ([k, l]) =>
              `<label>${l}<input data-k="${k}" data-i="${i}" data-r="smart" value="${esc(r[k] ?? "")}"></label>`,
          )
          .join(
            "",
          )}</div><label>Notas<textarea data-k="notes" data-i="${i}" data-r="smart">${esc(r.notes || "")}</textarea></label>`
      : kind === "ram"
        ? `<label>Módulo RAM<select data-k="ramComponentId" data-i="${i}" data-r="ram">${ramOptions(r)}</select></label><div class="formgrid">${[
            ["test", "Teste"],
            ["result", "Resultado"],
            ["errors", "Erros"],
          ]
            .map(
              ([k, l]) =>
                `<label>${l}<input data-k="${k}" data-i="${i}" data-r="ram" value="${esc(r[k] ?? "")}"></label>`,
            )
            .join(
              "",
            )}</div><label>Notas<textarea data-k="notes" data-i="${i}" data-r="ram">${esc(r.notes || "")}</textarea></label>`
        : defs
            .map(([k, l, t = "text"]) =>
              t === "textarea"
                ? `<label>${l}<textarea data-k="${k}" data-i="${i}" data-r="${kind}">${esc(r[k] || "")}</textarea></label>`
                : `<label>${l}<input data-k="${k}" data-i="${i}" data-r="${kind}" value="${esc(r[k] || "")}"></label>`,
            )
            .join("");
  return `<div class="repeat"><div class="line"><b>${kind === "smart" ? "Disco" : kind === "ram" ? "Módulo RAM" : " " + defs[0][1]} ${i + 1}</b><button type="button" class="danger" onclick="removeRow('${kind}',${i})">Remover</button></div>${fields}</div>`;
}
function renderRows(k) {
  document.querySelector("#rows-" + k).innerHTML = rows[k].length
    ? rows[k].map((r, i) => rowHtml(k, r, i)).join("")
    : '<div class="empty">Nenhum registo.</div>';
}
// P08: copia para rows[k] o que está escrito no ecrã. Tem de correr antes de qualquer
// re-render das linhas; caso contrário, o que foi escrito e ainda não gravado perde-se.
function syncRows(k) {
  document.querySelectorAll(`[data-r="${k}"]`).forEach((x) => {
    const row = rows[k][+x.dataset.i];
    if (row) row[x.dataset.k] = x.value;
  });
}
function collectRows(k) {
  syncRows(k);
  return rows[k].filter((r) => Object.values(r).some((v) => String(v || "").trim()));
}
function removeRow(k, i) {
  syncRows(k);
  rows[k].splice(i, 1);
  renderRows(k);
}
function addRow(k) {
  syncRows(k);
  rows[k].push(
    k === "smart"
      ? { deviceId: "", status: "" }
      : k === "ram"
        ? { ramComponentId: "", test: "MemTest86" }
        : k === "test"
          ? { tool: "SystemRescue" }
          : {},
  );
  renderRows(k);
}
// P08: o editor mostra também as preventivas já ligadas a este registo, mesmo que
// entretanto tenham deixado de estar associadas ao equipamento; assim não se perdem ao gravar.
function maintenancePlanOptions(e, r) {
  const linked = r.preventivePlanIds || [];
  const assigned = assignedPlans(e);
  const extra = preventivePlans.filter((p) => linked.includes(p.id) && !assigned.includes(p));
  return [...assigned.map((p) => [p, false]), ...extra.map((p) => [p, true])];
}
function editMaintenance(eid, mid) {
  let e = equipmentById(eid);
  maintenanceEquipmentId = eid;
  let m = mid
    ? e.maintenance.find((x) => x.id === mid)
    : {
        id: "m-" + Date.now(),
        date: localToday(),
        type: "Manutenção preventiva",
        description: "",
        areas: "",
        notes: "",
        checks: {},
        results: {},
      };
  let r = m.results || {};
  rows.smart = resolveSmartRows(e, r.smartDisks || []);
  rows.ram = resolveRamRows(e, r.ramRows || []);
  rows.temp = JSON.parse(JSON.stringify(r.tempRows || []));
  rows.test = JSON.parse(JSON.stringify(r.testRows || []));
  modal(
    `<header><div><span class="eyebrow">${mid ? "EDITAR" : "NOVO"}</span><h2>Registo de manutenção</h2></div><button onclick="requestCloseModal()">×</button></header><form id="mf" class="form"><div class="formgrid">${inputs(
      m,
      [
        ["date", "Data", "date"],
        ["type", "Tipo"],
        ["description", "O que foi feito"],
        ["areas", "Áreas"],
      ],
    )}</div><div class="preventive-picker"><div class="field-label">Preventivas realizadas</div><div class="assign-grid">${
      maintenancePlanOptions(e, r)
        .map(
          ([p, old]) =>
            `<label class="check"><input type="checkbox" name="preventivePlanIds" value="${esc(p.id)}" ${(r.preventivePlanIds || []).includes(p.id) ? "checked" : ""}> <span>${esc(p.name)}${old ? ' <small class="muted">(já não associada)</small>' : ""}</span></label>`,
        )
        .join("") || '<div class="empty">Nenhuma preventiva associada a este equipamento.</div>'
    }</div><small class="muted">Seleciona todas as preventivas realizadas nesta intervenção.</small></div><div class="tabs"><button type="button" class="tab on" onclick="tab(this,'checks')">Checklist</button><button type="button" class="tab" onclick="tab(this,'smart')">SMART</button><button type="button" class="tab" onclick="tab(this,'ram')">RAM</button><button type="button" class="tab" onclick="tab(this,'temp')">Temperaturas</button><button type="button" class="tab" onclick="tab(this,'test')">Testes</button></div><div id="tab-checks" class="tabbody">${checks.map((x) => `<label class="check"><input type="checkbox" name="chk-${x}" ${m.checks?.[x] ? "checked" : ""}> ${x}</label>`).join("")}</div>${["smart", "ram", "temp", "test"].map((k) => `<div id="tab-${k}" class="tabbody hidden"><div class="repeat-head"><button type="button" onclick="addRow('${k}')">+ Adicionar</button></div><div id="rows-${k}"></div></div>`).join("")}<label>Notas<textarea name="notes">${esc(m.notes || "")}</textarea></label></form><footer>${mid ? `<button class="danger" onclick="delMaintenance('${eid}','${mid}')">Apagar</button>` : ""}<span></span><button onclick="requestCloseModal()">Cancelar</button><button class="accent" onclick="saveMaintenance('${eid}','${mid || ""}','${m.id}')">Guardar</button></footer></section>`,
  );
  ["smart", "ram", "temp", "test"].forEach(renderRows);
}
function tab(btn, k) {
  document.querySelectorAll(".tab").forEach((x) => x.classList.remove("on"));
  btn.classList.add("on");
  document.querySelectorAll(".tabbody").forEach((x) => x.classList.add("hidden"));
  $("#tab-" + k).classList.remove("hidden");
}
async function saveMaintenance(eid, mid, tmp) {
  event.preventDefault();
  let e = equipmentById(eid),
    v = formVal("#mf");
  let smart = collectRows("smart");
  if (smart.some((d) => !d.deviceId))
    return fieldError(
      [...document.querySelectorAll("#rows-smart select")].find((x) => !x.value),
      "Seleciona o disco de cada registo SMART.",
    );
  let ids = smart.map((d) => d.deviceId);
  if (new Set(ids).size !== ids.length)
    return alert("Não podes registar o mesmo disco duas vezes na mesma manutenção.");
  smart = smart.map((d) => {
    let c = findComponent(d.deviceId)?.c;
    return { ...d, device: c ? smartDeviceLabel(c) : d.device || "" };
  });
  let ram = collectRows("ram");
  if (ram.some((r) => !r.ramComponentId))
    return fieldError(
      [...document.querySelectorAll("#rows-ram select")].find((x) => !x.value),
      "Seleciona o módulo RAM de cada registo.",
    );
  let ramIds = ram.map((r) => r.ramComponentId);
  if (new Set(ramIds).size !== ramIds.length)
    return alert("Não podes registar o mesmo módulo RAM duas vezes na mesma manutenção.");
  ram = ram.map((r) => {
    let c = findComponent(r.ramComponentId)?.c;
    return {
      ...r,
      module: c ? ramComponentLabel(c) : r.module || "",
      capacity: c?.capacity || r.capacity || "",
    };
  });
  let preventivePlanIds = [...document.querySelectorAll('[name="preventivePlanIds"]:checked')].map(
    (x) => x.value,
  );
  let checked = {};
  checks.forEach((x) => (checked[x] = $(`[name="chk-${x}"]`).checked));
  const record = {
    date: v.date,
    type: v.type,
    description: v.description,
    areas: v.areas,
    notes: v.notes,
    checks: checked,
    results: {
      preventivePlanIds,
      smartDisks: smart,
      ramRows: ram,
      tempRows: collectRows("temp"),
      testRows: collectRows("test"),
    },
  };
  const ok = await commit(() => {
    const target = equipmentById(eid);
    if (mid)
      Object.assign(
        target.maintenance.find((x) => x.id === mid),
        record,
      );
    else target.maintenance.push({ id: tmp, ...record });
  });
  if (!ok) return;
  closeModal();
  detail(eid);
}
async function delMaintenance(eid, mid) {
  if (!confirm("Apagar este registo de manutenção?")) return;
  let e = equipmentById(eid);
  if (!(await commit(() => (e.maintenance = e.maintenance.filter((x) => x.id !== mid))))) return;
  closeModal();
  detail(eid);
}
boot();
