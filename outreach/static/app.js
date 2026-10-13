"use strict";
const $ = (id) => document.getElementById(id);
let token = "",
  state = null,
  selected = null,
  poll = null,
  toastTimer,
  agentOriginal = null;
const styleLabels = {
  interest: "Interesse",
  origin: "Der Anfang",
  current_focus: "Aktuelles Thema",
  observation: "Beobachtung",
  curiosity: "Neugier",
  context: "Kontext",
};
const reasonLabels = {
  private_profile: "Privates Profil",
  no_usable_public_evidence: "Keine belastbaren öffentlichen Textbelege",
  no_target_keyword_match: "Keine passenden Zielkundenbegriffe",
  excluded_keyword_match: "Ausschlussbegriff vorhanden",
  followers_unknown: "Followerzahl für diesen Filter unbekannt",
  followers_out_of_range: "Followerzahl außerhalb deiner Auswahl",
  language_not_targeted: "Sprache außerhalb deiner Auswahl",
  unsupported_language: "Sprache wird noch nicht unterstützt",
  below_min_score: "Passungswert unter deiner Mindestgrenze",
  "No niche keyword filter configured":
    "Noch keine Zielkundenbegriffe festgelegt",
  "Usable public bio excerpt": "Verwertbarer Bio-Ausschnitt",
  "Usable public post excerpt": "Verwertbarer Post-Ausschnitt",
  "Follower count unknown": "Followerzahl unbekannt",
  "Language unverified; campaign default used for review":
    "Sprache ungeprüft: Standardsprache verwendet",
};
function reasonText(value) {
  return (
    reasonLabels[value] ||
    value
      .replace("Target keyword(s):", "Passende Begriffe:")
      .replace("Excluded keyword(s):", "Ausgeschlossene Begriffe:")
  );
}
const labels = {
  pending: "Neu",
  review: "Zur Prüfung",
  approved: "Freigegeben",
  sent: "Kontaktiert",
  replied: "Geantwortet",
  suppressed: "Gesperrt",
};
function el(tag, text, className) {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  if (className) n.className = className;
  return n;
}
function toast(message, error = false) {
  $("toast").textContent = message;
  $("toast").className = error ? "error" : "";
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(
    () => ($("toast").hidden = true),
    error ? 15000 : 6500,
  );
}
async function api(path, body) {
  const r = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      Authorization: "Bearer " + token,
      "Content-Type": "application/json",
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || "Anfrage fehlgeschlagen.");
  return data;
}
async function act(button, fn) {
  button.disabled = true;
  try {
    await fn();
    await refresh();
  } catch (e) {
    toast(e.message, true);
  } finally {
    button.disabled = false;
  }
}
function safeLink(url, label) {
  const a = el("a", label);
  try {
    const u = new URL(url);
    if (!["https:", "http:"].includes(u.protocol)) return el("span", label);
    a.href = u.href;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
  } catch {
    return el("span", label);
  }
  return a;
}
function badge(status) {
  return el("span", labels[status] || status, "badge " + status);
}
function button(text, cls, fn) {
  const b = el("button", text, cls);
  b.type = "button";
  b.addEventListener("click", () => act(b, () => fn(b)));
  return b;
}
async function refresh() {
  const fresh = await api("/api/state");
  const old = state;
  state = fresh;
  renderSummary();
  renderList();
  if (
    !old ||
    JSON.stringify(old.profiles.find((p) => p.username === selected)) !==
      JSON.stringify(state.profiles.find((p) => p.username === selected))
  )
    renderDetail();
}
function renderSummary() {
  $("campaign-label").textContent = state.campaign.name || state.campaign.id;
  $("setup-note").hidden = Boolean(
    state.campaign.sender?.name && state.campaign.offerings?.length,
  );
  const p = state.profiles;
  $("m-total").textContent = p.length;
  $("m-review").textContent = p.filter(
    (x) => x.status === "review" && x.drafts?.length,
  ).length;
  $("m-approved").textContent = p.filter((x) => x.status === "approved").length;
  $("m-sent").textContent = p.filter(
    (x) => ["sent", "replied"].includes(x.status) && !x.contacted_elsewhere,
  ).length;
  $("m-replied").textContent = p.filter(
    (x) => x.status === "replied" && !x.contacted_elsewhere,
  ).length;
  const active = state.jobs.filter((j) =>
    ["queued", "running"].includes(j.state),
  );
  const failed = state.jobs.filter((j) => j.state === "failed");
  $("job-note").textContent = active.length
    ? `${active.length} Aufgabe(n) werden vorbereitet. Du kannst weiterarbeiten.`
    : failed.length
      ? `${failed.length} Aufgabe(n) fehlgeschlagen. Betroffenes Profil öffnen und erneut vorbereiten.`
      : "Nach Passung sortiert. Der Wert ist eine Regelbewertung, keine Antwortwahrscheinlichkeit.";
  const previous = $("provider").value;
  $("provider").replaceChildren(
    ...state.providers.map((p) => {
      const o = el(
        "option",
        p === "template" ? "Vorlagen · offline" : "KI · OpenAI",
      );
      o.value = p;
      return o;
    }),
  );
  if (state.providers.includes(previous)) $("provider").value = previous;
}
function visibleProfiles() {
  const q = $("search").value.toLowerCase();
  return [...state.profiles]
    .filter(
      (x) =>
        ($("status-filter").value === "all" ||
          x.status === $("status-filter").value) &&
        `${x.username} ${x.profile.full_name || ""}`.toLowerCase().includes(q),
    )
    .sort((a, b) => (b.analysis?.score || 0) - (a.analysis?.score || 0));
}
function renderList() {
  const rows = visibleProfiles();
  $("profiles").replaceChildren();
  if (!rows.length) {
    const p = el(
      "p",
      state.profiles.length
        ? "Keine Profile für diesen Filter."
        : "Noch keine Profile importiert.",
      "notice small",
    );
    $("profiles").append(p);
    return;
  }
  for (const p of rows) {
    const b = el("button", undefined, "profile-row");
    b.type = "button";
    b.setAttribute("aria-pressed", String(selected === p.username));
    b.setAttribute("aria-label", `Profil ${p.username}, ${labels[p.status]}`);
    const name = p.profile.full_name || p.username;
    const initials = p.profile.full_name
      ? name
          .split(/\s+/)
          .map((x) => x[0])
          .slice(0, 2)
          .join("")
          .toUpperCase()
      : "@";
    b.append(el("span", initials, "avatar"));
    const info = el("div");
    info.append(
      el("div", name, "profile-name"),
      el("div", "@" + p.username, "profile-handle"),
      badge(p.status),
    );
    b.append(info, el("span", String(p.analysis?.score || 0), "score"));
    b.addEventListener("click", () => {
      selected = p.username;
      renderList();
      renderDetail();
    });
    $("profiles").append(b);
  }
}
function renderDetail() {
  const d = $("detail");
  const p = state?.profiles.find((x) => x.username === selected);
  if (!p) {
    d.replaceChildren();
    const empty = el("div", undefined, "empty");
    empty.append(
      el("span", "↗", "empty-symbol"),
      el(
        "h2",
        state.profiles.length
          ? "Wähle ein Profil."
          : "Deine Pipeline beginnt hier.",
      ),
      el(
        "p",
        "Öffne ein Profil, prüfe die öffentlichen Belege und wähle einen passenden Gesprächseinstieg.",
      ),
    );
    empty.append(
      button("Profile importieren", "secondary", async () =>
        $("import-dialog").showModal(),
      ),
    );
    d.append(empty);
    return;
  }
  d.replaceChildren();
  const head = el("div", undefined, "detail-head");
  const title = el("div");
  title.append(
    el("h2", p.profile.full_name || p.username),
    safeLink(p.profile.profile_url, "@" + p.username + " ↗"),
  );
  head.append(title, el("span", String(p.analysis?.score || 0), "score"));
  d.append(
    head,
    el("p", p.profile.bio || "Keine Profilbeschreibung vorhanden.", "bio"),
    badge(p.status),
  );
  const reasons = [
    ...(p.analysis?.reasons || []),
    ...(p.analysis?.blocked_reasons || []),
  ];
  if (reasons.length)
    d.append(el("p", reasons.map(reasonText).join(" · "), "reason"));
  if (p.contacted_elsewhere)
    d.append(
      el(
        "p",
        "Bereits über einen anderen Agenten / eine andere Kampagne kontaktiert. Hier wird kein neuer Erstkontakt vorbereitet.",
        "reason",
      ),
    );
  if (p.error) d.append(el("p", p.error, "reason danger"));
  const eh = el("div", undefined, "evidence-heading");
  eh.append(
    el("h3", "Öffentliche Belege"),
    el("span", "Vor Verwendung aktuell prüfen", "small"),
  );
  d.append(eh);
  const list = el("ul", undefined, "evidence");
  (p.analysis?.evidence || []).forEach((e, i) => {
    const li = el("li");
    li.append(
      el("span", String(i + 1).padStart(2, "0"), "ref"),
      el("p", e.text),
      safeLink(e.url, "Quelle ↗"),
    );
    list.append(li);
  });
  d.append(list);
  if (p.approved_draft && ["approved", "sent", "replied"].includes(p.status)) {
    const sh = el("div", undefined, "drafts-heading");
    sh.append(
      el("h3", "Dein freigegebener Einstieg"),
      el(
        "p",
        "Kopieren und in Instagram prüfen. Erst nach dem tatsächlichen Versand als kontaktiert markieren.",
      ),
    );
    d.append(sh, el("div", p.approved_draft.text, "approved-text"));
    const handoff = el("div", undefined, "handoff");
    handoff.append(
      button("Text kopieren", "primary", async () => {
        await navigator.clipboard.writeText(p.approved_draft.text);
        toast("Freigegebener Text kopiert.");
      }),
      safeLink(p.profile.profile_url, "Instagram öffnen ↗"),
    );
    if (p.status === "approved")
      handoff.append(
        button("Als gesendet markieren", "secondary", async () => {
          if (
            window.confirm(
              "Hast du diese Nachricht tatsächlich in Instagram gesendet?",
            )
          ) {
            await api(
              `/api/profile/${encodeURIComponent(p.username)}/sent`,
              {},
            );
            toast("Versand dokumentiert.");
          }
        }),
      );
    if (p.status === "sent")
      handoff.append(
        button("Antwort erhalten", "secondary", async () => {
          await api(
            `/api/profile/${encodeURIComponent(p.username)}/replied`,
            {},
          );
          toast("Antwort dokumentiert.");
        }),
      );
    d.append(handoff);
  } else if (["pending", "review"].includes(p.status)) {
    const sh = el("div", undefined, "drafts-heading");
    sh.append(
      el("h3", "Drei Wege ins Gespräch"),
      el(
        "p",
        "Eine Frage pro Einstieg. Texte bleiben bis zur Freigabe Entwürfe.",
      ),
    );
    d.append(sh);
    if (
      p.drafts?.length &&
      p.status === "review" &&
      state.providers.length > 1
    ) {
      d.append(
        button(
          "Mit gewähltem Textmodus vorbereiten",
          "secondary agent-retry",
          async () => {
            await api("/api/generate", {
              username: p.username,
              provider: $("provider").value,
            });
            toast(
              "Gewählter Textmodus angefordert. Derselbe Modus verwendet den vorhandenen Entwurf.",
            );
          },
        ),
      );
    }
    if (!p.drafts?.length && p.analysis?.eligible) {
      d.append(
        button("Einstiege vorbereiten", "primary", async () => {
          await api("/api/generate", {
            username: p.username,
            provider: $("provider").value,
          });
          toast("Aufgabe eingereiht.");
        }),
      );
    }
    for (const [i, draft] of (p.drafts || []).entries()) {
      const card = el("div", undefined, "draft");
      const meta = el("div", undefined, "draft-meta");
      meta.append(
        el(
          "strong",
          `${String(i + 1).padStart(2, "0")} · ${styleLabels[draft.style] || draft.style}`,
        ),
        el(
          "span",
          String(draft.methodology).includes("template")
            ? "Vorlage mit Profilbezug"
            : "KI-Entwurf",
        ),
      );
      const area = el("textarea");
      area.value = draft.text;
      area.setAttribute("aria-label", `Einstieg ${i + 1} bearbeiten`);
      const controls = el("div", undefined, "draft-controls");
      controls.append(
        el("span", "Zitierten Profilbezug beim Bearbeiten erhalten."),
        button("Prüfen & freigeben", "secondary", async () => {
          await api(`/api/profile/${encodeURIComponent(p.username)}/approve`, {
            draft_id: draft.id,
            text: area.value,
          });
          toast("Freigegeben. Noch keine Nachricht versendet.");
        }),
      );
      card.append(meta, area, controls);
      d.append(card);
    }
  }
  if (p.status !== "suppressed") {
    const end = el("div", undefined, "end-actions");
    end.append(
      button("Kontakt sperren", "quiet danger", async () => {
        if (
          window.confirm(
            "Diesen Kontakt dauerhaft aus der Erstkontakt-Pipeline ausschließen?",
          )
        ) {
          await api(
            `/api/profile/${encodeURIComponent(p.username)}/suppress`,
            {},
          );
          toast(
            "Kontakt gesperrt. Auch spätere Importe beachten diese Sperre.",
          );
        }
      }),
    );
    d.append(end);
  }
}
$("login-form").addEventListener("submit", (e) => {
  e.preventDefault();
  token = $("token").value.trim();
  act(e.submitter, async () => {
    await refresh();
    $("token").value = "";
    $("login").hidden = true;
    $("workspace").hidden = false;
    $("logout").hidden = false;
    clearInterval(poll);
    poll = setInterval(
      () =>
        refresh().catch(() => {
          clearInterval(poll);
          toast("Verbindung unterbrochen. Bitte neu verbinden.", true);
        }),
      4000,
    );
  });
});
$("logout").addEventListener("click", () => {
  token = "";
  state = null;
  selected = null;
  clearInterval(poll);
  $("workspace").hidden = true;
  $("logout").hidden = true;
  $("login").hidden = false;
  $("profiles").replaceChildren();
  $("detail").replaceChildren();
});
$("open-import").addEventListener("click", () =>
  $("import-dialog").showModal(),
);
$("empty-import").addEventListener("click", () =>
  $("import-dialog").showModal(),
);
$("close-import").addEventListener("click", () => $("import-dialog").close());
$("import-form").addEventListener("submit", (e) => {
  e.preventDefault();
  act(e.submitter, async () => {
    let raw = $("import-json").value;
    if ($("import-file").files.length) {
      const f = $("import-file").files[0];
      if (f.size > 2 * 1024 * 1024)
        throw new Error("Datei ist größer als 2 MB. Bitte aufteilen.");
      raw = await f.text();
    }
    const profiles = JSON.parse(raw);
    const result = await api("/api/import", { profiles });
    $("import-dialog").close();
    $("import-form").reset();
    toast(
      `${result.imported || 0} neue Profile, ${result.updated || 0} aktualisiert.`,
    );
  });
});
$("apify-import").addEventListener("click", (e) =>
  act(e.currentTarget, async () => {
    const result = await api("/api/import/apify", {
      dataset_id: $("dataset-id").value.trim(),
      max_items: Number($("dataset-limit").value),
    });
    $("import-dialog").close();
    toast(`${result.imported || 0} neue Profile importiert.`);
  }),
);
$("generate").addEventListener("click", (e) =>
  act(e.currentTarget, async () => {
    const result = await api("/api/generate", {
      provider: $("provider").value,
    });
    toast(`${result.jobs.length} Aufgabe(n) eingereiht.`);
  }),
);
$("search").addEventListener("input", renderList);
$("status-filter").addEventListener("change", renderList);
$("export").addEventListener("click", (e) =>
  act(e.currentTarget, async () => {
    const r = await fetch("/api/export", {
      headers: { Authorization: "Bearer " + token },
    });
    if (!r.ok) throw new Error("Export fehlgeschlagen.");
    const url = URL.createObjectURL(await r.blob());
    const a = el("a");
    a.href = url;
    a.download = "approved-outreach.csv";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast("CSV mit freigegebenen Nachrichten exportiert.");
  }),
);

$("open-agent").addEventListener("click", () => {
  const c = JSON.parse(JSON.stringify(state.campaign));
  agentOriginal = c;
  $("agent-name").value = c.name;
  $("sender-name").value = c.sender.name;
  $("sender-role").value = c.sender.role;
  $("sender-context").value =
    c.sender.context === "Vor Nutzung durch eigene, wahre Angaben ergänzen."
      ? ""
      : c.sender.context;
  $("offer").value = c.offerings.join("\n");
  $("niche").value = c.niche === "general" ? "" : c.niche;
  $("keywords").value = c.include_keywords.join(", ");
  $("exclusions").value = c.exclude_keywords.join(", ");
  $("agent-language").value = c.language;
  $("agent-tone").value = c.tone;
  $("questions").value = (c.hook_rules[0]?.questions[c.language] || []).join(
    "\n",
  );
  $("agent-dialog").showModal();
});
$("close-agent").addEventListener("click", () => $("agent-dialog").close());
$("agent-form").addEventListener("submit", (e) => {
  e.preventDefault();
  act(e.submitter, async () => {
    const split = (id) =>
      $(id)
        .value.split(",")
        .map((x) => x.trim())
        .filter(Boolean);
    const language = $("agent-language").value;
    const keywords = split("keywords");
    const questions = $("questions")
      .value.split("\n")
      .map((x) => x.trim())
      .filter(Boolean);
    if (questions.length && questions.length !== 3)
      throw new Error(
        "Bitte genau drei verschiedene offene Fragen eintragen oder das Feld leer lassen.",
      );
    if (questions.length && !keywords.length)
      throw new Error(
        "Für eigene Fragen bitte mindestens einen passenden Profilbegriff angeben.",
      );
    const original = agentOriginal || state.campaign;
    let hooks = JSON.parse(JSON.stringify(original.hook_rules));
    const originalQuestions = (hooks[0]?.questions[language] || []).join("\n");
    if ($("questions").value.trim() !== originalQuestions) {
      if (questions.length) {
        const first = hooks[0] || { keywords, questions: {} };
        hooks[0] = {
          ...first,
          keywords,
          questions: { ...first.questions, [language]: questions },
        };
      } else if (hooks[0]) {
        delete hooks[0].questions[language];
        if (!Object.keys(hooks[0].questions).length) hooks.shift();
      }
    }
    const campaign = {
      ...original,
      name: $("agent-name").value.trim(),
      niche: $("niche").value.trim(),
      sender: {
        name: $("sender-name").value.trim(),
        role: $("sender-role").value.trim(),
        context: $("sender-context").value.trim(),
      },
      offerings: $("offer")
        .value.split("\n")
        .map((x) => x.trim())
        .filter(Boolean),
      include_keywords: keywords,
      exclude_keywords: split("exclusions"),
      language,
      tone: $("agent-tone").value,
      hook_rules: hooks,
    };
    await api("/api/agent", campaign);
    $("agent-dialog").close();
    toast(
      "Dein Agent ist eingerichtet. Offene Profile wurden zur neuen Prüfung zurückgesetzt.",
    );
  });
});

$("agent-language").addEventListener("change", () => {
  $("questions").value = (
    agentOriginal?.hook_rules[0]?.questions[$("agent-language").value] || []
  ).join("\n");
});
