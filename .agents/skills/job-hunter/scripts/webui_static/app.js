(() => {
  "use strict";

  const STATUS_META = {
    candidates_extracted: { label: "À traiter", tone: "neutral", color: "#6c7280" },
    drafted: { label: "Brouillon prêt", tone: "primary", color: "#3f51b5" },
    sent: { label: "Envoyée", tone: "secondary", color: "#00796b" },
    interview: { label: "Entretien", tone: "tertiary", color: "#8e24aa" },
    offer: { label: "Offre reçue", tone: "success", color: "#146c2e" },
    rejected: { label: "Refusée", tone: "error", color: "#ba1a1a" },
    ignored: { label: "Ignorée", tone: "neutral", color: "#8d8d96" },
    rejected_school: { label: "École / CFA", tone: "warning", color: "#b26a00" },
  };

  const EDITABLE_STATUSES = [
    "candidates_extracted",
    "drafted",
    "sent",
    "interview",
    "offer",
    "rejected",
    "ignored",
  ];

  const CHANNEL_LABELS = {
    email: "Email direct",
    ats: "ATS entreprise",
    jobboard: "Jobboard",
  };

  const SOURCE_LABELS = {
    wttj: "Welcome to the Jungle",
    hellowork: "HelloWork",
    indeed: "Indeed",
    linkedin: "LinkedIn",
  };

  const VIEW_TITLES = {
    tasks: "Tâches à faire",
    pipeline: "Envois & Suivi",
    offers: "Offres scrappées",
    drafts: "Brouillons générés",
    runs: "Runs de scraping",
  };

  const state = {
    data: null,
    view: "tasks",
    search: "",
    followUpDays: Number(localStorage.getItem("jh_followup_days") || "7") || 7,
    pipelineMode: localStorage.getItem("jh_pipeline_mode") || "kanban",
    pipelineIncludeIgnored: false,
    filters: {
      status: "all",
      source: "",
      contract: "",
      channel: "",
      sort: "score_desc",
      includeSchools: false,
    },
    currentOfferId: null,
    currentOfferDetail: null,
    snackbarTimer: null,
  };

  /* ========================================================================
     Safe DOM & Formatting Helpers (Zero innerHTML on untrusted data)
     ======================================================================== */
  function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [key, val] of Object.entries(attrs)) {
      if (val === null || val === undefined || val === false) continue;
      if (key === "class") {
        node.className = String(val);
      } else if (key === "text") {
        node.textContent = String(val);
      } else if (key === "dataset" && typeof val === "object") {
        for (const [dk, dv] of Object.entries(val)) {
          node.dataset[dk] = String(dv);
        }
      } else if (key.startsWith("on") && typeof val === "function") {
        node.addEventListener(key.slice(2).toLowerCase(), val);
      } else if (val === true) {
        node.setAttribute(key, "");
      } else {
        node.setAttribute(key, String(val));
      }
    }
    const list = Array.isArray(children) ? children : [children];
    for (const child of list) {
      if (child === null || child === undefined || child === false) continue;
      node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    }
    return node;
  }

  function clearNode(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function isSafeHttpUrl(raw) {
    if (!raw || typeof raw !== "string") return false;
    try {
      const parsed = new URL(raw);
      return parsed.protocol === "http:" || parsed.protocol === "https:";
    } catch {
      return false;
    }
  }

  function formatDateTime(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return new Intl.DateTimeFormat("fr-FR", {
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(d);
  }

  function formatRelativeDays(iso, fallbackDays) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "—";
    const diffDays =
      typeof fallbackDays === "number"
        ? fallbackDays
        : Math.max(0, Math.floor((Date.now() - d.getTime()) / 86_400_000));
    if (diffDays === 0) return "Aujourd'hui";
    if (diffDays === 1) return "Hier";
    return `Il y a ${diffDays} j`;
  }

  function formatBytes(bytes) {
    if (!bytes || bytes <= 0) return "0 o";
    if (bytes < 1024) return `${bytes} o`;
    return `${(bytes / 1024).toFixed(1)} Ko`;
  }

  function makeBadge(text, tone = "neutral") {
    return el("span", { class: `badge badge-${tone}`, text });
  }

  function makeStatusBadge(status) {
    const meta = STATUS_META[status] || { label: status, tone: "neutral" };
    return makeBadge(meta.label, meta.tone);
  }

  function makeScorePill(score) {
    if (score === null || score === undefined) {
      return el("span", { class: "score-pill score-none", text: "—", title: "Offre non scorée" });
    }
    const cls = score >= 70 ? "score-high" : score >= 45 ? "score-mid" : "score-low";
    return el("span", { class: `score-pill ${cls}`, text: `${score}` });
  }

  function matchesSearch(item, query) {
    if (!query) return true;
    const q = query.toLowerCase();
    const haystack = [
      item.company,
      item.title,
      item.location,
      item.source,
      item.contract_type,
      item.contact_email,
      item.notes,
      item.reason,
      item.draft_subject,
      ...(item.matched_skills || []),
      ...(item.missing_skills || []),
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return haystack.includes(q);
  }

  /* ========================================================================
     Theme & Accent Management
     ======================================================================== */
  function applyTheme(theme) {
    const normalized = ["auto", "light", "dark"].includes(theme) ? theme : "auto";
    document.documentElement.dataset.theme = normalized;
    localStorage.setItem("jh_theme", normalized);

    const btn = document.getElementById("theme-toggle-btn");
    const labels = { auto: "Thème : Auto (système)", light: "Thème : Clair", dark: "Thème : Sombre" };
    if (btn) btn.title = `${labels[normalized]} — cliquer pour changer`;
  }

  function applyAccent(accent) {
    const normalized = ["indigo", "teal", "orchid", "terracotta"].includes(accent) ? accent : "indigo";
    document.documentElement.dataset.accent = normalized;
    localStorage.setItem("jh_accent", normalized);
    document.querySelectorAll(".accent-dot").forEach((dot) => {
      dot.classList.toggle("is-active", dot.dataset.accentPick === normalized);
    });
  }

  /* ========================================================================
     Snackbar & Clipboard
     ======================================================================== */
  function showSnackbar(message, { actionLabel = "", onAction = null, duration = 4500 } = {}) {
    const bar = document.getElementById("snackbar");
    const textNode = document.getElementById("snackbar-text");
    const actionBtn = document.getElementById("snackbar-action");

    if (state.snackbarTimer) clearTimeout(state.snackbarTimer);
    textNode.textContent = message;

    const freshBtn = actionBtn.cloneNode(true);
    actionBtn.replaceWith(freshBtn);

    if (actionLabel && typeof onAction === "function") {
      freshBtn.textContent = actionLabel;
      freshBtn.classList.remove("is-hidden");
      freshBtn.addEventListener("click", () => {
        bar.classList.add("is-hidden");
        onAction();
      });
    } else {
      freshBtn.classList.add("is-hidden");
    }

    bar.classList.remove("is-hidden");
    state.snackbarTimer = setTimeout(() => {
      bar.classList.add("is-hidden");
    }, duration);
  }

  async function copyText(text, successMsg = "Copié dans le presse-papiers") {
    try {
      await navigator.clipboard.writeText(text);
      showSnackbar(successMsg);
    } catch {
      showSnackbar("Impossible d'accéder au presse-papiers");
    }
  }

  /* ========================================================================
     API Calls
     ======================================================================== */
  async function loadDashboard({ silent = false } = {}) {
    try {
      const res = await fetch(`/api/dashboard?follow_up_days=${encodeURIComponent(state.followUpDays)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      state.data = await res.json();
      populateDynamicFilterOptions();
      renderAll();
      if (!silent) showSnackbar("Données actualisées", { duration: 2000 });
    } catch (err) {
      showSnackbar(`Erreur de chargement : ${err.message}`);
    }
  }

  async function updateOfferStatus(offerId, newStatus, { notes = undefined, previousStatus = null } = {}) {
    const payload = {};
    if (newStatus !== undefined && newStatus !== null) payload.status = newStatus;
    if (notes !== undefined) payload.notes = notes;

    try {
      const res = await fetch(`/api/offers/${encodeURIComponent(offerId)}/status`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error || `HTTP ${res.status}`);

      await loadDashboard({ silent: true });

      if (state.currentOfferId === offerId && document.getElementById("offer-dialog").open) {
        await loadOfferModalData(offerId);
      }

      const statusLabel = newStatus ? (STATUS_META[newStatus]?.label || newStatus) : "Notes";
      const msg = newStatus ? `Statut mis à jour : ${statusLabel}` : "Notes enregistrées";

      if (previousStatus && newStatus && previousStatus !== newStatus) {
        showSnackbar(msg, {
          actionLabel: "Annuler",
          onAction: () => updateOfferStatus(offerId, previousStatus),
        });
      } else {
        showSnackbar(msg);
      }
      return true;
    } catch (err) {
      showSnackbar(`Erreur : ${err.message}`);
      return false;
    }
  }

  /* ========================================================================
     View Switching & Header Updates
     ======================================================================== */
  function setView(viewName) {
    if (!VIEW_TITLES[viewName]) return;
    state.view = viewName;

    document.querySelectorAll(".nav-item").forEach((btn) => {
      const active = btn.dataset.view === viewName;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
    });

    document.querySelectorAll(".view-section").forEach((sec) => {
      sec.classList.toggle("is-active", sec.id === `view-${viewName}`);
    });

    document.getElementById("page-title").textContent = VIEW_TITLES[viewName];
    renderActiveView();
  }

  function populateDynamicFilterOptions() {
    if (!state.data) return;
    const offers = state.data.offers || [];

    const sources = [...new Set(offers.map((o) => o.source).filter(Boolean))].sort();
    const contracts = [...new Set(offers.map((o) => o.contract_type).filter(Boolean))].sort();

    const sourceSel = document.getElementById("filter-source");
    const prevSource = sourceSel.value;
    clearNode(sourceSel);
    sourceSel.appendChild(el("option", { value: "", text: "Toutes les sources" }));
    for (const s of sources) {
      sourceSel.appendChild(el("option", { value: s, text: SOURCE_LABELS[s] || s }));
    }
    sourceSel.value = sources.includes(prevSource) ? prevSource : "";

    const contractSel = document.getElementById("filter-contract");
    const prevContract = contractSel.value;
    clearNode(contractSel);
    contractSel.appendChild(el("option", { value: "", text: "Tous les contrats" }));
    for (const c of contracts) {
      contractSel.appendChild(el("option", { value: c, text: c.toUpperCase() }));
    }
    contractSel.value = contracts.includes(prevContract) ? prevContract : "";
  }

  function updateHeaderAndBadges() {
    const data = state.data;
    if (!data) return;
    const stats = data.stats;
    const cand = data.candidate || {};

    const subParts = [];
    if (cand.full_name) subParts.push(cand.full_name);
    subParts.push(`${stats.total_tracked} offres suivies`);
    subParts.push(`Maj ${formatDateTime(data.generated_at)}`);
    document.getElementById("page-subtitle").textContent = subParts.join(" · ");

    const setBadge = (id, count) => {
      const node = document.getElementById(id);
      if (!node) return;
      node.textContent = count > 99 ? "99+" : String(count);
      node.classList.toggle("is-hidden", count <= 0);
    };

    const byStatus = stats.by_status || {};
    const activePipelineCount =
      (byStatus.drafted || 0) + (byStatus.sent || 0) + (byStatus.interview || 0) + (byStatus.offer || 0);

    setBadge("badge-tasks", stats.action_required_count || 0);
    setBadge("badge-pipeline", activePipelineCount);
    setBadge("badge-offers", stats.total_tracked || 0);
    setBadge("badge-drafts", stats.drafts_count || 0);
    setBadge("badge-runs", stats.runs_count || 0);
  }

  function renderAll() {
    if (!state.data) return;
    updateHeaderAndBadges();
    renderActiveView();
  }

  function renderActiveView() {
    if (!state.data) return;
    if (state.view === "tasks") renderTasksView();
    else if (state.view === "pipeline") renderPipelineView();
    else if (state.view === "offers") renderOffersView();
    else if (state.view === "drafts") renderDraftsView();
    else if (state.view === "runs") renderRunsView();
  }

  /* ========================================================================
     View 1: À faire (Dashboard & Action Queue)
     ======================================================================== */
  function renderTasksView() {
    const { stats, tasks, candidate } = state.data;
    const byStatus = stats.by_status || {};

    const kpiGrid = document.getElementById("kpi-grid");
    clearNode(kpiGrid);

    const draftsCount = (tasks.drafts_to_send || []).length;
    const followUpsCount = (tasks.follow_ups || []).length;
    const toReviewCount = (tasks.to_review || []).length;
    const schoolsCount = byStatus.rejected_school || 0;

    const kpis = [
      {
        title: "Brouillons à envoyer",
        value: draftsCount,
        hint: draftsCount > 0 ? "Prêts avec CV PDF joint" : "Aucun brouillon en attente",
        tone: draftsCount > 0 ? "tone-primary" : "",
        onClick: () => setView("drafts"),
      },
      {
        title: `Relances J+${state.followUpDays}`,
        value: followUpsCount,
        hint: followUpsCount > 0 ? "Envoyées sans réponse" : "Aucune relance en retard",
        tone: followUpsCount > 0 ? "tone-error" : "",
        onClick: () => setView("pipeline"),
      },
      {
        title: "Candidatures envoyées",
        value: stats.applied_total || 0,
        hint: `Taux positif : ${stats.positive_rate_pct || 0}% (${stats.positive_responses || 0} entretiens/offres)`,
        tone: (stats.positive_responses || 0) > 0 ? "tone-tertiary" : "",
        onClick: () => setView("pipeline"),
      },
      {
        title: "Offres à trier",
        value: toReviewCount,
        hint: `${stats.with_email || 0} avec email direct`,
        tone: "",
        onClick: () => {
          state.filters.status = "candidates_extracted";
          setView("offers");
        },
      },
      {
        title: "Écoles / CFA bloquées",
        value: schoolsCount,
        hint: "Filtrées automatiquement",
        tone: "",
        onClick: () => {
          state.filters.includeSchools = true;
          state.filters.status = "rejected_school";
          document.getElementById("filter-include-schools").checked = true;
          setView("offers");
        },
      },
    ];

    for (const kpi of kpis) {
      kpiGrid.appendChild(
        el(
          "button",
          { type: "button", class: `kpi-card ${kpi.tone}`, onClick: kpi.onClick },
          [
            el("div", { class: "kpi-top" }, [el("span", { text: kpi.title })]),
            el("div", { class: "kpi-value", text: String(kpi.value) }),
            el("div", { class: "kpi-hint", text: kpi.hint }),
          ]
        )
      );
    }

    // 1. Brouillons prêts à envoyer
    const filteredDrafts = (tasks.drafts_to_send || []).filter((o) => matchesSearch(o, state.search));
    document.getElementById("count-drafts-todo").textContent = String(filteredDrafts.length);
    const draftsListNode = document.getElementById("list-drafts-todo");
    clearNode(draftsListNode);

    if (filteredDrafts.length === 0) {
      draftsListNode.appendChild(
        el("div", { class: "empty-state" }, [
          el("strong", { text: "Aucun brouillon en attente d'envoi" }),
          "Générez un brouillon via l'agent ou la commande job-hunter draft.",
        ])
      );
    } else {
      for (const item of filteredDrafts) {
        const metaItems = [
          item.draft_to ? `Destinataire : ${item.draft_to}` : `Canal : ${CHANNEL_LABELS[item.apply_channel] || item.apply_channel}`,
          item.location || null,
          formatRelativeDays(item.updated_at, item.days_since_update),
        ].filter(Boolean);

        const actionBtns = [
          el("button", {
            type: "button",
            class: "md-btn md-btn-tonal md-btn-sm",
            text: "Voir brouillon",
            onClick: () => openOfferModal(item.id, "draft"),
          }),
        ];
        if (item.eml_download_url) {
          actionBtns.push(
            el("a", {
              href: item.eml_download_url,
              class: "md-btn md-btn-outlined md-btn-sm",
              text: "Télécharger .eml",
            })
          );
        }
        actionBtns.push(
          el("button", {
            type: "button",
            class: "md-btn md-btn-filled md-btn-sm",
            text: "Marquer envoyé",
            onClick: () => updateOfferStatus(item.id, "sent", { previousStatus: item.status }),
          })
        );

        draftsListNode.appendChild(
          el("div", { class: "task-item" }, [
            el("div", { class: "task-main" }, [
              el("div", { class: "task-title-row" }, [
                el("span", { class: "task-company", text: item.company }),
                el("span", { class: "task-title", text: `— ${item.title}` }),
                item.has_cv_attachment ? makeBadge("CV PDF joint", "success") : makeBadge("Sans CV joint", "warning"),
              ]),
              item.draft_subject
                ? el("div", { class: "task-meta", text: `Objet : ${item.draft_subject}` })
                : null,
              el("div", { class: "task-meta", text: metaItems.join(" · ") }),
            ]),
            el("div", { class: "task-actions" }, actionBtns),
          ])
        );
      }
    }

    // 2. Relances à effectuer
    document.getElementById("followup-subtitle").textContent =
      `Candidatures envoyées sans réponse depuis ≥ ${state.followUpDays} jours`;
    const filteredFollowUps = (tasks.follow_ups || []).filter((o) => matchesSearch(o, state.search));
    document.getElementById("count-followups-todo").textContent = String(filteredFollowUps.length);
    const followUpsNode = document.getElementById("list-followups-todo");
    clearNode(followUpsNode);

    if (filteredFollowUps.length === 0) {
      followUpsNode.appendChild(
        el("div", { class: "empty-state" }, [
          el("strong", { text: "Aucune relance en retard" }),
          `Toutes vos candidatures envoyées ont moins de ${state.followUpDays} jours.`,
        ])
      );
    } else {
      for (const item of filteredFollowUps) {
        followUpsNode.appendChild(
          el("div", { class: "task-item" }, [
            el("div", { class: "task-main" }, [
              el("div", { class: "task-title-row" }, [
                makeBadge(`J+${item.days_since_update}`, "error"),
                el("span", { class: "task-company", text: item.company }),
                el("span", { class: "task-title", text: `— ${item.title}` }),
              ]),
              el("div", {
                class: "task-meta",
                text: [item.contact_email || "Via plateforme", item.notes || null].filter(Boolean).join(" · "),
              }),
            ]),
            el("div", { class: "task-actions" }, [
              el("button", {
                type: "button",
                class: "md-btn md-btn-tonal md-btn-sm",
                text: "Entretien",
                onClick: () => updateOfferStatus(item.id, "interview", { previousStatus: item.status }),
              }),
              el("button", {
                type: "button",
                class: "md-btn md-btn-outlined md-btn-sm",
                text: "Détails",
                onClick: () => openOfferModal(item.id, "details"),
              }),
            ]),
          ])
        );
      }
    }

    // 3. Offres scrappées à trier / rédiger
    const filteredReview = (tasks.to_review || []).filter((o) => matchesSearch(o, state.search));
    document.getElementById("count-review-todo").textContent = String(filteredReview.length);
    const reviewNode = document.getElementById("list-review-todo");
    clearNode(reviewNode);

    if (filteredReview.length === 0) {
      reviewNode.appendChild(
        el("div", { class: "empty-state" }, [
          el("strong", { text: "Aucune nouvelle offre à trier" }),
          "Lancez un scraping via job-hunter search pour découvrir de nouvelles opportunités.",
        ])
      );
    } else {
      for (const item of filteredReview.slice(0, 10)) {
        const metaBits = [
          SOURCE_LABELS[item.source] || item.source,
          item.contract_type ? item.contract_type.toUpperCase() : "",
          item.location,
          item.contact_email || CHANNEL_LABELS[item.apply_channel] || item.apply_channel,
        ].filter(Boolean);

        reviewNode.appendChild(
          el("div", { class: "task-item" }, [
            el("div", { class: "task-main" }, [
              el("div", { class: "task-title-row" }, [
                makeScorePill(item.match_score),
                el("span", { class: "task-company", text: item.company }),
                el("span", { class: "task-title", text: `— ${item.title}` }),
                item.security_warnings && item.security_warnings.length
                  ? makeBadge("Alerte sécurité", "error")
                  : null,
              ]),
              el("div", { class: "task-meta", text: metaBits.join(" · ") }),
              item.matched_skills && item.matched_skills.length
                ? el(
                    "div",
                    { class: "skill-tags" },
                    item.matched_skills.slice(0, 6).map((s) => el("span", { class: "skill-tag matched", text: s }))
                  )
                : null,
            ]),
            el("div", { class: "task-actions" }, [
              el("button", {
                type: "button",
                class: "md-btn md-btn-tonal md-btn-sm",
                text: "Inspecter",
                onClick: () => openOfferModal(item.id, "details"),
              }),
              el("button", {
                type: "button",
                class: "md-btn md-btn-text md-btn-sm",
                text: "Ignorer",
                onClick: () => updateOfferStatus(item.id, "ignored", { previousStatus: item.status }),
              }),
            ]),
          ])
        );
      }
      if (filteredReview.length > 10) {
        reviewNode.appendChild(
          el("div", { class: "empty-state" }, [
            el("button", {
              type: "button",
              class: "md-btn md-btn-outlined",
              text: `Voir les ${filteredReview.length} offres à traiter`,
              onClick: () => {
                state.filters.status = "candidates_extracted";
                setView("offers");
              },
            }),
          ])
        );
      }
    }

    // Aside 1: Entonnoir
    const funnelNode = document.getElementById("funnel-chart");
    clearNode(funnelNode);
    const totalNonSchool = Math.max(1, (stats.total_tracked || 0) - (byStatus.rejected_school || 0));
    const funnelSteps = [
      { label: "Retenues (hors écoles)", value: totalNonSchool, cls: "secondary" },
      { label: "Brouillons générés", value: (byStatus.drafted || 0) + (stats.applied_total || 0), cls: "" },
      { label: "Candidatures envoyées", value: stats.applied_total || 0, cls: "tertiary" },
      { label: "Entretiens", value: byStatus.interview || 0, cls: "success" },
      { label: "Offres reçues", value: byStatus.offer || 0, cls: "success" },
    ];
    for (const step of funnelSteps) {
      const pct = Math.min(100, Math.round((step.value / totalNonSchool) * 100));
      funnelNode.appendChild(
        el("div", { class: "bar-row" }, [
          el("div", { class: "bar-label-line" }, [
            el("span", { text: step.label }),
            el("strong", { text: String(step.value) }),
          ]),
          el("progress", {
            class: `md-progress ${step.cls}`.trim(),
            max: "100",
            value: String(step.value > 0 ? Math.max(4, pct) : 0),
            "aria-label": `${step.label} : ${step.value}`,
          }),
        ])
      );
    }

    // Aside 2: Sources
    const sourcesNode = document.getElementById("sources-chart");
    clearNode(sourcesNode);
    const bySource = stats.by_source || {};
    const sourceEntries = Object.entries(bySource).sort((a, b) => b[1] - a[1]);
    const maxSource = Math.max(1, ...sourceEntries.map(([, v]) => v));
    if (sourceEntries.length === 0) {
      sourcesNode.appendChild(el("div", { class: "empty-state", text: "Aucune source enregistrée" }));
    } else {
      for (const [src, count] of sourceEntries) {
        const pct = Math.round((count / maxSource) * 100);
        const label = SOURCE_LABELS[src] || src;
        sourcesNode.appendChild(
          el("div", { class: "bar-row" }, [
            el("div", { class: "bar-label-line" }, [
              el("span", { text: label }),
              el("strong", { text: String(count) }),
            ]),
            el("progress", {
              class: "md-progress",
              max: "100",
              value: String(Math.max(4, pct)),
              "aria-label": `${label} : ${count}`,
            }),
          ])
        );
      }
    }

    // Aside 3: Profile health
    const healthNode = document.getElementById("profile-health");
    clearNode(healthNode);
    healthNode.appendChild(
      el("div", { class: "health-row" }, [
        el("span", { text: "Profil candidat" }),
        candidate.configured ? makeBadge("Configuré", "success") : makeBadge("Incomplet", "warning"),
      ])
    );
    healthNode.appendChild(
      el("div", { class: "health-row" }, [
        el("span", { text: "CV PDF vérifié" }),
        candidate.cv_valid
          ? makeBadge(candidate.cv_filename || "Valide", "success")
          : makeBadge("Manquant", "warning"),
      ])
    );
    if (stats.avg_score !== null && stats.avg_score !== undefined) {
      healthNode.appendChild(
        el("div", { class: "health-row" }, [
          el("span", { text: "Score moyen" }),
          makeScorePill(Math.round(stats.avg_score)),
        ])
      );
    }
    healthNode.appendChild(
      el("div", {
        class: "cli-snippet",
        text: "uv run --project .agents/skills/job-hunter job-hunter search --from-profile",
        title: "Commande CLI pour lancer une nouvelle recherche",
      })
    );
  }

  /* ========================================================================
     View 2: Envois & Suivi (Kanban / Table)
     ======================================================================== */
  function makeInlineStatusSelect(offer) {
    const sel = el("select", {
      class: "inline-status-select",
      "aria-label": `Changer le statut de ${offer.company}`,
      onChange: (e) => {
        const next = e.target.value;
        if (next && next !== offer.status) {
          updateOfferStatus(offer.id, next, { previousStatus: offer.status });
        }
      },
    });
    for (const st of EDITABLE_STATUSES) {
      sel.appendChild(el("option", { value: st, text: STATUS_META[st].label, selected: offer.status === st }));
    }
    return sel;
  }

  function renderPipelineView() {
    const offers = (state.data.offers || []).filter((o) => matchesSearch(o, state.search));
    const kanbanEl = document.getElementById("pipeline-kanban");
    const tableWrap = document.getElementById("pipeline-table-wrap");

    document.querySelectorAll("[data-pipeline-mode]").forEach((btn) => {
      btn.classList.toggle("is-selected", btn.dataset.pipelineMode === state.pipelineMode);
    });

    const columns = ["drafted", "sent", "interview", "offer", "rejected"];
    if (state.pipelineIncludeIgnored) columns.push("ignored");

    const pipelineOffers = offers.filter((o) => columns.includes(o.status));

    if (state.pipelineMode === "kanban") {
      kanbanEl.classList.remove("is-hidden");
      tableWrap.classList.add("is-hidden");
      clearNode(kanbanEl);

      for (const colStatus of columns) {
        const meta = STATUS_META[colStatus];
        const colItems = pipelineOffers.filter((o) => o.status === colStatus);

        const cardsWrap = el("div", { class: "kanban-cards" });
        if (colItems.length === 0) {
          cardsWrap.appendChild(el("div", { class: "empty-state", text: "Aucune offre" }));
        } else {
          for (const item of colItems) {
            cardsWrap.appendChild(
              el("article", { class: "kanban-card" }, [
                el("div", { class: "kanban-card-head" }, [
                  el("div", {}, [
                    el("div", {
                      class: "kanban-card-company",
                      text: item.company,
                      onClick: () => openOfferModal(item.id, item.has_draft ? "draft" : "details"),
                    }),
                    el("div", {
                      class: "kanban-card-title",
                      text: item.title,
                      onClick: () => openOfferModal(item.id, "details"),
                    }),
                  ]),
                  item.needs_follow_up ? makeBadge(`J+${item.days_since_update}`, "error") : makeScorePill(item.match_score),
                ]),
                el("div", {
                  class: "task-meta",
                  text: [
                    item.contract_type ? item.contract_type.toUpperCase() : "",
                    item.contact_email || CHANNEL_LABELS[item.apply_channel] || item.apply_channel,
                  ]
                    .filter(Boolean)
                    .join(" · "),
                }),
                item.notes ? el("div", { class: "kanban-card-notes", text: item.notes }) : null,
                el("div", { class: "kanban-card-footer" }, [
                  makeInlineStatusSelect(item),
                  el("button", {
                    type: "button",
                    class: "md-btn md-btn-text md-btn-sm",
                    text: "Ouvrir",
                    onClick: () => openOfferModal(item.id, item.has_draft ? "draft" : "details"),
                  }),
                ]),
              ])
            );
          }
        }

        kanbanEl.appendChild(
          el("section", { class: "kanban-col" }, [
            el("div", { class: "kanban-col-header" }, [
              el("div", { class: "kanban-col-title" }, [
                el("span", { class: `kanban-dot status-dot-${colStatus}` }),
                el("span", { text: meta.label }),
              ]),
              el("span", { class: "pill-counter secondary", text: String(colItems.length) }),
            ]),
            cardsWrap,
          ])
        );
      }
    } else {
      kanbanEl.classList.add("is-hidden");
      tableWrap.classList.remove("is-hidden");
      const tbody = document.getElementById("pipeline-table-body");
      clearNode(tbody);

      if (pipelineOffers.length === 0) {
        tbody.appendChild(
          el("tr", {}, [
            el("td", { colspan: "7" }, [
              el("div", { class: "empty-state", text: "Aucune candidature dans le pipeline pour le moment." }),
            ]),
          ])
        );
        return;
      }

      for (const item of pipelineOffers) {
        tbody.appendChild(
          el("tr", {}, [
            el("td", {}, [
              el("div", {
                class: "offer-cell-title",
                text: `${item.company} — ${item.title}`,
                onClick: () => openOfferModal(item.id, item.has_draft ? "draft" : "details"),
              }),
              el("div", {
                class: "offer-cell-sub",
                text: [item.contract_type.toUpperCase(), item.location].filter(Boolean).join(" · "),
              }),
            ]),
            el("td", {}, [
              makeInlineStatusSelect(item),
              item.needs_follow_up ? el("div", { class: "mt-xs" }, [makeBadge(`Relance J+${item.days_since_update}`, "error")]) : null,
            ]),
            el("td", {}, [makeScorePill(item.match_score)]),
            el("td", {}, [
              el("div", { text: item.contact_email || CHANNEL_LABELS[item.apply_channel] || item.apply_channel }),
              item.has_eml ? makeBadge("draft.eml prêt", "primary") : null,
            ]),
            el("td", { text: item.notes || "—" }),
            el("td", { text: formatRelativeDays(item.updated_at, item.days_since_update), title: formatDateTime(item.updated_at) }),
            el("td", { class: "col-actions" }, [
              el("button", {
                type: "button",
                class: "md-btn md-btn-tonal md-btn-sm",
                text: "Détails",
                onClick: () => openOfferModal(item.id, item.has_draft ? "draft" : "details"),
              }),
            ]),
          ])
        );
      }
    }
  }

  /* ========================================================================
     View 3: Offres déjà scrappées
     ======================================================================== */
  function renderOffersView() {
    const allOffers = state.data.offers || [];

    // Status Filter Chips
    const chipsContainer = document.getElementById("status-filter-chips");
    clearNode(chipsContainer);
    const visiblePool = state.filters.includeSchools
      ? allOffers
      : allOffers.filter((o) => o.status !== "rejected_school");

    const counts = { all: visiblePool.length };
    for (const o of visiblePool) {
      counts[o.status] = (counts[o.status] || 0) + 1;
    }

    const chipStatuses = [
      { key: "all", label: "Toutes" },
      { key: "candidates_extracted", label: "À traiter" },
      { key: "drafted", label: "Brouillons" },
      { key: "sent", label: "Envoyées" },
      { key: "interview", label: "Entretiens" },
      { key: "offer", label: "Offres" },
      { key: "rejected", label: "Refusées" },
      { key: "ignored", label: "Ignorées" },
    ];
    if (state.filters.includeSchools) {
      chipStatuses.push({ key: "rejected_school", label: "Écoles / CFA" });
    }

    for (const ch of chipStatuses) {
      const c = counts[ch.key] || 0;
      chipsContainer.appendChild(
        el(
          "button",
          {
            type: "button",
            class: `filter-chip ${state.filters.status === ch.key ? "is-selected" : ""}`,
            onClick: () => {
              state.filters.status = ch.key;
              renderOffersView();
            },
          },
          [el("span", { text: ch.label }), el("span", { class: "chip-count", text: `(${c})` })]
        )
      );
    }

    // Apply Filters
    let filtered = visiblePool.filter((o) => {
      if (state.filters.status !== "all" && o.status !== state.filters.status) return false;
      if (state.filters.source && o.source !== state.filters.source) return false;
      if (state.filters.contract && o.contract_type !== state.filters.contract) return false;
      if (state.filters.channel && o.apply_channel !== state.filters.channel) return false;
      return matchesSearch(o, state.search);
    });

    // Apply Sort
    const sortBy = state.filters.sort;
    filtered = [...filtered].sort((a, b) => {
      if (sortBy === "score_desc") {
        const sa = a.match_score ?? -1;
        const sb = b.match_score ?? -1;
        if (sb !== sa) return sb - sa;
        return (b.updated_at || "").localeCompare(a.updated_at || "");
      }
      if (sortBy === "updated_desc") {
        return (b.updated_at || "").localeCompare(a.updated_at || "");
      }
      if (sortBy === "company_asc") {
        return (a.company || "").localeCompare(b.company || "", "fr");
      }
      if (sortBy === "status") {
        return (a.status || "").localeCompare(b.status || "");
      }
      return 0;
    });

    document.getElementById("offers-results-meta").textContent =
      `${filtered.length} offre${filtered.length > 1 ? "s" : ""} affichée${filtered.length > 1 ? "s" : ""} sur ${visiblePool.length}`;

    const tbody = document.getElementById("offers-table-body");
    clearNode(tbody);

    if (filtered.length === 0) {
      tbody.appendChild(
        el("tr", {}, [
          el("td", { colspan: "6" }, [
            el("div", { class: "empty-state", text: "Aucune offre ne correspond à vos filtres actuels." }),
          ]),
        ])
      );
      return;
    }

    for (const item of filtered) {
      const subParts = [
        item.company,
        item.location || null,
        item.contract_type ? item.contract_type.toUpperCase() : null,
      ].filter(Boolean);

      const titleChildren = [
        el("span", {
          class: "offer-cell-title",
          text: item.title,
          onClick: () => openOfferModal(item.id, "details"),
        }),
        el("div", { class: "offer-cell-sub", text: subParts.join(" · ") }),
      ];

      if (item.reason) {
        titleChildren.push(
          el("div", { class: "offer-cell-sub" }, [makeBadge(`Motif : ${item.reason}`, "warning")])
        );
      }
      if (item.security_warnings && item.security_warnings.length) {
        titleChildren.push(
          el("div", { class: "offer-cell-sub" }, [
            makeBadge(`Alerte : ${item.security_warnings.join(", ")}`, "error"),
          ])
        );
      }
      if (item.matched_skills && item.matched_skills.length) {
        titleChildren.push(
          el(
            "div",
            { class: "skill-tags" },
            item.matched_skills.slice(0, 5).map((s) => el("span", { class: "skill-tag matched", text: s }))
          )
        );
      }

      const sourceCellChildren = [
        makeBadge(SOURCE_LABELS[item.source] || item.source, "neutral"),
        el("div", { class: "offer-cell-sub" }, [
          el("span", { text: CHANNEL_LABELS[item.apply_channel] || item.apply_channel }),
        ]),
      ];
      if (item.contact_email) {
        sourceCellChildren.push(
          el("button", {
            type: "button",
            class: "md-btn md-btn-text md-btn-sm inline-link-btn",
            text: item.contact_email,
            title: "Cliquer pour copier l'email",
            onClick: () => copyText(item.contact_email, `Email copié : ${item.contact_email}`),
          })
        );
      }

      const statusCell =
        item.status === "rejected_school" ? makeStatusBadge(item.status) : makeInlineStatusSelect(item);

      const actionChildren = [
        el("button", {
          type: "button",
          class: "md-btn md-btn-tonal md-btn-sm",
          text: "Voir",
          onClick: () => openOfferModal(item.id, "details"),
        }),
      ];
      if (isSafeHttpUrl(item.url)) {
        actionChildren.push(
          el("a", {
            href: item.url,
            target: "_blank",
            rel: "noopener noreferrer",
            class: "md-btn md-btn-outlined md-btn-sm",
            text: "Annonce",
          })
        );
      }

      tbody.appendChild(
        el("tr", {}, [
          el("td", {}, [makeScorePill(item.match_score)]),
          el("td", {}, titleChildren),
          el("td", {}, sourceCellChildren),
          el("td", {}, [statusCell]),
          el("td", {
            text: formatRelativeDays(item.updated_at, item.days_since_update),
            title: formatDateTime(item.updated_at),
          }),
          el("td", { class: "col-actions" }, [el("div", { class: "table-actions" }, actionChildren)]),
        ])
      );
    }
  }

  /* ========================================================================
     View 4: Brouillons générés
     ======================================================================== */
  function renderDraftsView() {
    const grid = document.getElementById("drafts-grid");
    clearNode(grid);

    const drafts = (state.data.drafts || []).filter((d) =>
      matchesSearch(
        {
          company: d.company,
          title: d.title,
          contact_email: d.to,
          draft_subject: d.subject,
          location: d.location,
        },
        state.search
      )
    );

    if (drafts.length === 0) {
      grid.appendChild(
        el("div", { class: "md-card md-card-outlined empty-state" }, [
          el("strong", { text: "Aucun brouillon trouvé dans drafts/" }),
          "Les fichiers application.md et draft.eml générés par job-hunter apparaîtront ici.",
        ])
      );
      return;
    }

    for (const d of drafts) {
      const actions = [];
      if (d.offer_id) {
        actions.push(
          el("button", {
            type: "button",
            class: "md-btn md-btn-tonal md-btn-sm",
            text: "Aperçu complet",
            onClick: () => openOfferModal(d.offer_id, "draft"),
          })
        );
      }
      if (d.eml_download_url) {
        actions.push(
          el("a", {
            href: d.eml_download_url,
            class: "md-btn md-btn-outlined md-btn-sm",
            text: "Télécharger .eml",
          })
        );
      }
      if (d.offer_id && d.status === "drafted") {
        actions.push(
          el("button", {
            type: "button",
            class: "md-btn md-btn-filled md-btn-sm",
            text: "Marquer envoyé",
            onClick: () => updateOfferStatus(d.offer_id, "sent", { previousStatus: d.status }),
          })
        );
      }

      grid.appendChild(
        el("article", { class: "md-card md-card-elevated draft-card" }, [
          el("div", { class: "card-header card-header-compact" }, [
            el("div", {}, [
              el("h3", { class: "card-title", text: d.company }),
              el("p", { class: "card-subtitle", text: d.title }),
            ]),
            makeStatusBadge(d.status),
          ]),
          el("div", { class: "task-meta" }, [
            makeBadge(d.date, "neutral"),
            d.has_cv_attachment ? makeBadge("CV PDF joint", "success") : makeBadge("Sans CV PDF", "warning"),
            d.to ? el("span", { text: `À : ${d.to}` }) : el("span", { text: "Destinataire via formulaire" }),
          ]),
          d.subject ? el("div", { class: "draft-subject", text: d.subject }) : null,
          d.body_preview ? el("p", { class: "draft-preview", text: d.body_preview }) : null,
          el("div", { class: "draft-actions" }, actions),
        ])
      );
    }
  }

  /* ========================================================================
     View 5: Runs de scraping
     ======================================================================== */
  function renderRunsView() {
    const container = document.getElementById("runs-list");
    clearNode(container);

    const runs = (state.data.runs || []).filter((r) => {
      if (!state.search) return true;
      const q = state.search.toLowerCase();
      return [r.run_id, r.location, ...(r.queries || []), ...(r.contracts || [])]
        .join(" ")
        .toLowerCase()
        .includes(q);
    });

    if (runs.length === 0) {
      container.appendChild(
        el("div", { class: "md-card md-card-outlined empty-state" }, [
          el("strong", { text: "Aucun run de scraping enregistré" }),
          "Lancez job-hunter search pour alimenter l'historique des runs.",
        ])
      );
      return;
    }

    for (const run of runs) {
      const title =
        (run.queries && run.queries.length ? run.queries.join(", ") : "Recherche") +
        (run.location ? ` · ${run.location}` : "");

      const sourceCards = (run.sources || []).map((s) => {
        const tone = s.status === "ok" ? "success" : s.status === "blocked_antibot" ? "warning" : "error";
        const statusLabel =
          s.status === "ok" ? `OK (${s.scraped})` : s.status === "blocked_antibot" ? "Bloqué anti-bot" : "Erreur";
        return el("div", { class: "source-card" }, [
          el("div", { class: "source-card-top" }, [
            el("span", { text: SOURCE_LABELS[s.source] || s.source }),
            makeBadge(statusLabel, tone),
          ]),
          s.error ? el("div", { class: "source-error", text: s.error }) : null,
        ]);
      });

      const rejected = run.rejected_school_details || [];
      let rejectedNode = null;
      if (rejected.length > 0) {
        rejectedNode = el("details", { class: "rejected-details" }, [
          el("summary", { text: `Voir les ${rejected.length} écoles / CFA filtrées dans ce run` }),
          el(
            "ul",
            { class: "rejected-list" },
            rejected.map((r) =>
              el("li", {
                text: `${r.company} — ${r.title} (${SOURCE_LABELS[r.source] || r.source}) · ${r.reason}`,
              })
            )
          ),
        ]);
      }

      container.appendChild(
        el("article", { class: "md-card md-card-outlined run-card" }, [
          el("div", { class: "card-header card-header-compact" }, [
            el("div", {}, [
              el("h3", { class: "card-title", text: title }),
              el("p", {
                class: "card-subtitle",
                text: `${formatDateTime(run.timestamp)} · Run ${run.run_id}`,
              }),
            ]),
            el(
              "div",
              { class: "header-right-actions" },
              (run.contracts || []).map((c) => makeBadge(c.toUpperCase(), "primary"))
            ),
          ]),
          el("div", { class: "run-stats-row" }, [
            el("span", { class: "run-stat-chip", text: `Scrappées : ${run.stats.scraped}` }),
            el("span", { class: "run-stat-chip", text: `Retenues : ${run.stats.kept}` }),
            el("span", { class: "run-stat-chip", text: `Écoles filtrées : ${run.stats.rejected_schools}` }),
            el("span", { class: "run-stat-chip", text: `Doublons : ${run.stats.duplicates}` }),
          ]),
          sourceCards.length ? el("div", { class: "run-sources-grid" }, sourceCards) : null,
          rejectedNode,
        ])
      );
    }
  }

  /* ========================================================================
     Offer Detail & Draft Modal (<dialog>)
     ======================================================================== */
  function setDialogTab(tabName) {
    document.querySelectorAll(".dialog-tab").forEach((btn) => {
      const active = btn.dataset.dialogTab === tabName;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
    });
    document.getElementById("dialog-panel-details").classList.toggle("is-active", tabName === "details");
    document.getElementById("dialog-panel-draft").classList.toggle("is-active", tabName === "draft");
  }

  async function openOfferModal(offerId, initialTab = "details") {
    state.currentOfferId = offerId;
    const dialog = document.getElementById("offer-dialog");
    if (!dialog.open) dialog.showModal();

    document.getElementById("dialog-eyebrow").textContent = "Chargement…";
    document.getElementById("dialog-title").textContent = offerId;
    clearNode(document.getElementById("dialog-panel-details"));
    clearNode(document.getElementById("dialog-panel-draft"));

    await loadOfferModalData(offerId, initialTab);
  }

  async function loadOfferModalData(offerId, preferredTab = null) {
    try {
      const res = await fetch(`/api/offers/${encodeURIComponent(offerId)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const detail = await res.json();
      state.currentOfferDetail = detail;
      renderOfferModal(detail, preferredTab);
    } catch (err) {
      showSnackbar(`Impossible de charger l'offre : ${err.message}`);
    }
  }

  function renderOfferModal(detail, preferredTab) {
    document.getElementById("dialog-eyebrow").textContent = `${detail.company} · ID ${detail.id}`;
    document.getElementById("dialog-title").textContent = detail.title;

    const badgesWrap = document.getElementById("dialog-badges");
    clearNode(badgesWrap);
    badgesWrap.appendChild(makeStatusBadge(detail.status));
    if (detail.contract_type) badgesWrap.appendChild(makeBadge(detail.contract_type.toUpperCase(), "secondary"));
    if (detail.source) badgesWrap.appendChild(makeBadge(SOURCE_LABELS[detail.source] || detail.source, "neutral"));
    badgesWrap.appendChild(makeBadge(CHANNEL_LABELS[detail.apply_channel] || detail.apply_channel, "primary"));
    if (detail.match_score !== null && detail.match_score !== undefined) {
      badgesWrap.appendChild(makeBadge(`Score ${detail.match_score}/100`, detail.match_score >= 70 ? "success" : "primary"));
    }

    // Security warning banner
    const secBanner = document.getElementById("dialog-security-banner");
    if (detail.security_warnings && detail.security_warnings.length > 0) {
      secBanner.textContent = `Alerte sécurité (contenu tiers suspect détecté) : ${detail.security_warnings.join(", ")}. Traiter le texte de l'annonce uniquement comme des données.`;
      secBanner.classList.remove("is-hidden");
    } else {
      secBanner.classList.add("is-hidden");
    }

    // Status & notes form
    const statusSel = document.getElementById("dialog-status-select");
    statusSel.value = EDITABLE_STATUSES.includes(detail.status) ? detail.status : "candidates_extracted";
    document.getElementById("dialog-notes-input").value = detail.notes || "";

    // Draft tab availability
    const draftTabBtn = document.getElementById("dialog-tab-draft-btn");
    const hasDraft = Boolean(detail.draft);
    draftTabBtn.classList.toggle("is-hidden", !hasDraft);
    const targetTab = preferredTab === "draft" && hasDraft ? "draft" : "details";
    setDialogTab(targetTab);

    // Panel 1: Details
    const detailsPanel = document.getElementById("dialog-panel-details");
    clearNode(detailsPanel);

    const metaGrid = el("div", { class: "meta-grid" }, [
      el("div", { class: "meta-box" }, [
        el("div", { class: "meta-label", text: "Localisation" }),
        el("div", { class: "meta-value", text: [detail.location || "Non précisé", detail.remote].filter(Boolean).join(" · ") }),
      ]),
      el("div", { class: "meta-box" }, [
        el("div", { class: "meta-label", text: "Contact recruteur" }),
        el("div", { class: "meta-value" }, [
          detail.contact_email
            ? el("button", {
                type: "button",
                class: "md-btn md-btn-text md-btn-sm inline-link-btn",
                text: `${detail.contact_email} (copier)`,
                onClick: () => copyText(detail.contact_email, `Email copié : ${detail.contact_email}`),
              })
            : "Aucun email direct",
        ]),
      ]),
      el("div", { class: "meta-box" }, [
        el("div", { class: "meta-label", text: "Salaire / Langue" }),
        el("div", { class: "meta-value", text: `${detail.salary || "Non précisé"} · ${(detail.language || "fr").toUpperCase()}` }),
      ]),
      el("div", { class: "meta-box" }, [
        el("div", { class: "meta-label", text: "Suivi" }),
        el("div", {
          class: "meta-value",
          text: `Maj ${formatDateTime(detail.updated_at)} (${formatRelativeDays(detail.updated_at, detail.days_since_update)})`,
        }),
      ]),
    ]);
    detailsPanel.appendChild(metaGrid);

    if ((detail.matched_skills && detail.matched_skills.length) || (detail.missing_skills && detail.missing_skills.length)) {
      const skillsCard = el("div", { class: "meta-box" }, [
        el("div", { class: "meta-label", text: "Analyse des compétences" }),
        el("div", { class: "skill-tags mt-sm" }, [
          ...(detail.matched_skills || []).map((s) => el("span", { class: "skill-tag matched", text: `✓ ${s}` })),
          ...(detail.missing_skills || []).map((s) => el("span", { class: "skill-tag", text: `+ ${s}` })),
        ]),
      ]);
      detailsPanel.appendChild(skillsCard);
    }

    if (detail.reason) {
      detailsPanel.appendChild(
        el("div", { class: "meta-box" }, [
          el("div", { class: "meta-label", text: "Motif de filtrage" }),
          el("div", { class: "meta-value", text: detail.reason }),
        ])
      );
    }

    detailsPanel.appendChild(
      el("div", {}, [
        el("div", { class: "meta-label mb-sm", text: "Description de l'annonce (texte brut sécurisé)" }),
        el("div", {
          class: "description-box",
          text: detail.description || "Aucune description complète enregistrée pour cette offre.",
        }),
      ])
    );

    // Panel 2: Draft
    const draftPanel = document.getElementById("dialog-panel-draft");
    clearNode(draftPanel);
    if (detail.draft) {
      const eml = detail.draft.eml || {};
      const attSummary =
        eml.attachments && eml.attachments.length
          ? eml.attachments.map((a) => `${a.filename} (${formatBytes(a.size_bytes)})`).join(", ")
          : "Aucune pièce jointe";

      draftPanel.appendChild(
        el("div", { class: "meta-grid" }, [
          el("div", { class: "meta-box" }, [
            el("div", { class: "meta-label", text: "Destinataire" }),
            el("div", { class: "meta-value", text: eml.to || detail.contact_email || "Candidature via formulaire" }),
          ]),
          el("div", { class: "meta-box" }, [
            el("div", { class: "meta-label", text: "Pièces jointes (.eml)" }),
            el("div", { class: "meta-value", text: attSummary }),
          ]),
        ])
      );

      if (eml.subject) {
        draftPanel.appendChild(
          el("div", { class: "meta-box" }, [
            el("div", { class: "meta-label", text: "Objet de l'email" }),
            el("div", { class: "meta-value", text: eml.subject }),
          ])
        );
      }

      const bodyText = eml.body || detail.draft.markdown || "";
      draftPanel.appendChild(
        el("div", {}, [
          el("div", { class: "meta-label mb-sm", text: "Corps du message" }),
          el("div", { class: "email-preview-box", text: bodyText }),
        ])
      );

      const draftActions = el("div", { class: "footer-group" }, [
        eml.subject
          ? el("button", {
              type: "button",
              class: "md-btn md-btn-outlined md-btn-sm",
              text: "Copier l'objet",
              onClick: () => copyText(eml.subject, "Objet copié"),
            })
          : null,
        bodyText
          ? el("button", {
              type: "button",
              class: "md-btn md-btn-tonal md-btn-sm",
              text: "Copier le message",
              onClick: () => copyText(bodyText, "Corps de l'email copié"),
            })
          : null,
        detail.draft.eml_download_url
          ? el("a", {
              href: detail.draft.eml_download_url,
              class: "md-btn md-btn-filled md-btn-sm",
              text: "Télécharger draft.eml",
            })
          : null,
      ]);

      const toEmail = eml.to || detail.contact_email;
      if (toEmail) {
        const mailto = `mailto:${encodeURIComponent(toEmail)}?subject=${encodeURIComponent(eml.subject || "")}&body=${encodeURIComponent(bodyText)}`;
        draftActions.appendChild(
          el("a", {
            href: mailto,
            class: "md-btn md-btn-outlined md-btn-sm",
            text: "Ouvrir mailto:",
          })
        );
      }
      draftPanel.appendChild(draftActions);
    }

    // Footer links & quick actions
    const footer = document.getElementById("dialog-footer");
    clearNode(footer);

    const leftLinks = el("div", { class: "footer-group" });
    if (isSafeHttpUrl(detail.url)) {
      leftLinks.appendChild(
        el("a", {
          href: detail.url,
          target: "_blank",
          rel: "noopener noreferrer",
          class: "md-btn md-btn-outlined md-btn-sm",
          text: "Ouvrir l'annonce",
        })
      );
    }
    if (isSafeHttpUrl(detail.apply_url) && detail.apply_url !== detail.url) {
      leftLinks.appendChild(
        el("a", {
          href: detail.apply_url,
          target: "_blank",
          rel: "noopener noreferrer",
          class: "md-btn md-btn-tonal md-btn-sm",
          text: "Lien candidature directe",
        })
      );
    }

    const rightBtns = el("div", { class: "footer-group" });
    if (detail.status === "drafted") {
      rightBtns.appendChild(
        el("button", {
          type: "button",
          class: "md-btn md-btn-filled md-btn-sm",
          text: "Marquer envoyé",
          onClick: () => updateOfferStatus(detail.id, "sent", { previousStatus: detail.status }),
        })
      );
    }
    footer.appendChild(leftLinks);
    footer.appendChild(rightBtns);
  }

  /* ========================================================================
     Event Wiring & Initialization
     ======================================================================== */
  function init() {
    applyTheme(localStorage.getItem("jh_theme") || "auto");
    applyAccent(localStorage.getItem("jh_accent") || "indigo");

    const followUpSel = document.getElementById("followup-days-select");
    followUpSel.value = String(state.followUpDays);
    followUpSel.addEventListener("change", () => {
      state.followUpDays = Number(followUpSel.value) || 7;
      localStorage.setItem("jh_followup_days", String(state.followUpDays));
      loadDashboard({ silent: true });
    });

    // Navigation rail
    document.querySelectorAll(".nav-item").forEach((btn) => {
      btn.addEventListener("click", () => setView(btn.dataset.view));
    });

    // Accent dots
    document.querySelectorAll(".accent-dot").forEach((dot) => {
      dot.addEventListener("click", () => applyAccent(dot.dataset.accentPick));
    });

    // Theme cycle (auto -> light -> dark -> auto)
    document.getElementById("theme-toggle-btn").addEventListener("click", () => {
      const order = ["auto", "light", "dark"];
      const current = document.documentElement.dataset.theme || "auto";
      const next = order[(order.indexOf(current) + 1) % order.length];
      applyTheme(next);
      const names = { auto: "Auto (système)", light: "Clair", dark: "Sombre" };
      showSnackbar(`Thème : ${names[next]}`, { duration: 1800 });
    });

    // Refresh
    document.getElementById("refresh-btn").addEventListener("click", () => loadDashboard());

    // Global Search
    const searchInput = document.getElementById("global-search");
    const clearSearchBtn = document.getElementById("clear-search-btn");
    searchInput.addEventListener("input", () => {
      state.search = searchInput.value.trim();
      clearSearchBtn.classList.toggle("is-hidden", !state.search);
      renderActiveView();
    });
    clearSearchBtn.addEventListener("click", () => {
      searchInput.value = "";
      state.search = "";
      clearSearchBtn.classList.add("is-hidden");
      renderActiveView();
      searchInput.focus();
    });

    // Dashboard button -> all offers
    document.getElementById("btn-see-all-offers").addEventListener("click", () => {
      state.filters.status = "candidates_extracted";
      setView("offers");
    });

    // Pipeline controls
    document.querySelectorAll("[data-pipeline-mode]").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.pipelineMode = btn.dataset.pipelineMode;
        localStorage.setItem("jh_pipeline_mode", state.pipelineMode);
        renderPipelineView();
      });
    });
    document.getElementById("pipeline-include-ignored").addEventListener("change", (e) => {
      state.pipelineIncludeIgnored = e.target.checked;
      renderPipelineView();
    });

    // Offers filters
    document.getElementById("filter-source").addEventListener("change", (e) => {
      state.filters.source = e.target.value;
      renderOffersView();
    });
    document.getElementById("filter-contract").addEventListener("change", (e) => {
      state.filters.contract = e.target.value;
      renderOffersView();
    });
    document.getElementById("filter-channel").addEventListener("change", (e) => {
      state.filters.channel = e.target.value;
      renderOffersView();
    });
    document.getElementById("filter-sort").addEventListener("change", (e) => {
      state.filters.sort = e.target.value;
      renderOffersView();
    });
    document.getElementById("filter-include-schools").addEventListener("change", (e) => {
      state.filters.includeSchools = e.target.checked;
      if (!e.target.checked && state.filters.status === "rejected_school") {
        state.filters.status = "all";
      }
      renderOffersView();
    });
    document.getElementById("reset-filters-btn").addEventListener("click", () => {
      state.filters = {
        status: "all",
        source: "",
        contract: "",
        channel: "",
        sort: "score_desc",
        includeSchools: false,
      };
      document.getElementById("filter-source").value = "";
      document.getElementById("filter-contract").value = "";
      document.getElementById("filter-channel").value = "";
      document.getElementById("filter-sort").value = "score_desc";
      document.getElementById("filter-include-schools").checked = false;
      searchInput.value = "";
      state.search = "";
      clearSearchBtn.classList.add("is-hidden");
      renderOffersView();
    });

    // Dialog controls
    const dialog = document.getElementById("offer-dialog");
    document.getElementById("dialog-close-btn").addEventListener("click", () => dialog.close());
    // Light dismiss on backdrop click
    dialog.addEventListener("click", (e) => {
      if (e.target === dialog) dialog.close();
    });
    document.querySelectorAll(".dialog-tab").forEach((btn) => {
      btn.addEventListener("click", () => setDialogTab(btn.dataset.dialogTab));
    });
    document.getElementById("dialog-status-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!state.currentOfferId) return;
      const newStatus = document.getElementById("dialog-status-select").value;
      const newNotes = document.getElementById("dialog-notes-input").value;
      const prev = state.currentOfferDetail ? state.currentOfferDetail.status : null;
      await updateOfferStatus(state.currentOfferId, newStatus, { notes: newNotes, previousStatus: prev });
    });

    // Keyboard shortcuts
    document.addEventListener("keydown", (e) => {
      const tag = (e.target && e.target.tagName) || "";
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(tag);
      if (typing) return;
      if (e.key === "/") {
        e.preventDefault();
        searchInput.focus();
      } else if (e.key === "r" && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        loadDashboard();
      }
    });

    loadDashboard({ silent: true });
  }

  document.addEventListener("DOMContentLoaded", init);
})();
