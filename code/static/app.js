async function api(url, options) {
  const response = await fetch(url, options);
  let payload;
  try {
    payload = await response.json();
  } catch (error) {
    throw new Error(`服务返回了无效响应（HTTP ${response.status}）`);
  }
  return { response, payload };
}

function submitTime() {
  return document.getElementById("time").value.replace("T", " ");
}

function showMessage(message, isError = true) {
  const element = document.getElementById("msg");
  element.textContent = message || "";
  element.className = isError ? "error" : "success";
}

function clearResult() {
  document.getElementById("result").replaceChildren();
  document.getElementById("reasons").replaceChildren();
}

function renderResult(data) {
  const result = document.getElementById("result");
  result.replaceChildren();
  [["T日", data.T_date], ["确认日", data.confirm_date], ["查询日", data.query_date]].forEach(([label, value]) => {
    const card = document.createElement("div");
    card.className = "card";
    const title = document.createElement("span");
    title.textContent = label;
    const date = document.createElement("b");
    date.textContent = value;
    card.append(title, date);
    result.appendChild(card);
  });

  const reasons = document.getElementById("reasons");
  reasons.replaceChildren();
  (data.reasons || []).forEach((reason) => {
    const item = document.createElement("li");
    item.textContent = reason;
    reasons.appendChild(item);
  });
}

async function query() {
  clearResult();
  showMessage("");
  try {
    const { payload } = await api("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        submit_time: submitTime(),
        fund_type: document.getElementById("type").value,
      }),
    });
    if (!payload.success) {
      showMessage(payload.error?.message || "查询失败");
      return;
    }
    renderResult(payload.data);
    showMessage("查询成功", false);
    await loadHistory();
  } catch (error) {
    showMessage(error.message);
  }
}

function renderHistory(rows) {
  const container = document.getElementById("history");
  container.replaceChildren();
  if (!rows.length) {
    const empty = document.createElement("p");
    empty.textContent = "暂无历史查询记录";
    container.appendChild(empty);
    return;
  }
  const table = document.createElement("table");
  const header = document.createElement("tr");
  ["时间", "类型", "请求", "结果"].forEach((value) => {
    const cell = document.createElement("th");
    cell.textContent = value;
    header.appendChild(cell);
  });
  table.appendChild(header);
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    [row.created_at, row.kind, JSON.stringify(row.request), JSON.stringify(row.result || row.error)].forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      tr.appendChild(cell);
    });
    table.appendChild(tr);
  });
  container.appendChild(table);
}

async function loadHistory() {
  try {
    const { payload } = await api("/api/history?limit=50");
    if (!payload.success) {
      showMessage(payload.error?.message || "历史记录加载失败");
      return;
    }
    renderHistory(payload.data || []);
  } catch (error) {
    showMessage(error.message);
  }
}

document.getElementById("query-button").addEventListener("click", query);
document.getElementById("history-button").addEventListener("click", loadHistory);
loadHistory();
