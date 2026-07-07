let current = null;

async function loadQueue() {
  const cases = await (await fetch("/api/cases")).json();
  const rows = document.getElementById("rows");
  rows.innerHTML = "";
  for (const c of cases) {
    const div = document.createElement("div");
    div.className = "row" + (current === c.case_id ? " active" : "");
    div.innerHTML =
      `<span class="id">${c.case_id}</span>` +
      `<span class="badge pattern">${c.pattern}</span>` +
      `<span class="badge ${c.status}">${c.status}</span>` +
      `<div class="muted">${c.account_id} on ${c.instrument}, score ${c.score.toFixed(2)}, ` +
      `recommends ${c.recommendation}</div>`;
    div.onclick = () => openCase(c.case_id);
    rows.appendChild(div);
  }
}

async function openCase(id) {
  current = id;
  const c = await (await fetch(`/api/cases/${id}`)).json();
  const m = c.memo;
  const evidence = m.evidence
    .map((e) => `<tr><td>${e.claim}</td><td><code>${e.source}</code></td><td>${e.value}</td></tr>`)
    .join("");
  const pending = c.status === "pending";
  document.getElementById("detail").innerHTML = `
    <h2>${m.headline}</h2>
    <p class="muted">${c.case_id} | window ${m.window}s | drafted by ${c.generated_by} |
      confidence ${m.confidence.toFixed(2)} | recommends <b>${m.recommendation}</b></p>
    <div class="note">Draft case package. Approving records your review; it does not file
      anything. Filing, if warranted, happens in the firm's regulatory workflow.</div>
    <h3>Narrative</h3>
    ${pending ? `<textarea id="narrative">${m.narrative}</textarea>` : `<p>${m.narrative}</p>`}
    <h3>Evidence</h3>
    <table><tr><th>Claim</th><th>Source</th><th>Value</th></tr>${evidence}</table>
    <h3>Regulatory references</h3>
    <p>${m.regulatory_references.join("; ") || "none"}</p>
    <h3>Review</h3>
    ${
      pending
        ? `<textarea id="notes" placeholder="Reviewer notes"></textarea><br><br>
           <button id="approve">Approve draft</button>
           <button id="dismiss">Dismiss</button>`
        : `<p>Status: <b>${c.status}</b>. Notes: ${c.reviewer_notes || "none"}</p>`
    }`;
  if (pending) {
    document.getElementById("approve").onclick = () => review(id, "approve");
    document.getElementById("dismiss").onclick = () => review(id, "dismiss");
  }
  loadQueue();
}

async function review(id, action) {
  const original = (await (await fetch(`/api/cases/${id}`)).json()).memo.narrative;
  const edited = document.getElementById("narrative").value;
  await fetch(`/api/cases/${id}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      action,
      notes: document.getElementById("notes").value,
      edited_narrative: edited !== original ? edited : null,
    }),
  });
  openCase(id);
}

loadQueue();
