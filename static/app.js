const request = async (url, options = {}) => {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
};

function employeeRow(employee) {
  const row = document.createElement("tr");
  // Usa textContent para que nomes e tags sejam tratados como texto, nunca como HTML.
  for (const value of [
    employee.name,
    employee.employee_id,
    employee.tag,
    employee.role || "—",
    employee.allowed ? "Authorized" : "Not authorized",
    employee.active ? "Active" : "Inactive",
  ]) {
    const cell = document.createElement("td");
    cell.textContent = value;
    row.append(cell);
  }

  const actions = document.createElement("td");
  const edit = document.createElement("button");
  edit.type = "button";
  edit.textContent = "Edit";
  edit.addEventListener("click", () => editEmployee(employee));
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "button-secondary";
  remove.textContent = "Delete";
  remove.addEventListener("click", () => removeEmployee(employee.id));
  actions.append(edit, remove);
  row.append(actions);
  return row;
}

async function refresh() {
  try {
    const [people, events, occupants] = await Promise.all([
      request("/api/employees"),
      request("/api/events"),
      request("/api/occupancy"),
    ]);
    const employeeTable = document.querySelector("#employees");
    employeeTable.replaceChildren(...people.map(employeeRow));
    document.querySelector("#occupancy").textContent =
      occupants.map((person) => person.name).join(", ") || "None";
    document.querySelector("#events").textContent = events
      .map((event) => `${event.occurred_at} · ${event.employee_name || "Unknown"} · ${event.type} · ${event.outcome}`)
      .join("\n");
  } catch (error) {
    if (error.message === "Login required") window.location.assign("/login");
  }
}

async function editEmployee(employee) {
  const name = window.prompt("Name", employee.name);
  if (name === null) return;
  const employee_id = window.prompt("Employee ID", employee.employee_id);
  if (employee_id === null) return;
  const tag = window.prompt("RFID tag", employee.tag);
  if (tag === null) return;
  const role = window.prompt("Role", employee.role);
  if (role === null) return;

  try {
    await request(`/api/employees/${employee.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        name,
        employee_id,
        tag,
        role,
        allowed: window.confirm("Allow room access?"),
        active: window.confirm("Employee active?"),
      }),
    });
    await refresh();
  } catch (error) {
    window.alert(error.message);
  }
}

async function removeEmployee(id) {
  try {
    await request(`/api/employees/${id}`, { method: "DELETE" });
    await refresh();
  } catch (error) {
    window.alert(error.message);
  }
}

document.querySelector("#employee-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const message = document.querySelector("#employee-message");
  try {
    await request("/api/employees", {
      method: "POST",
      body: JSON.stringify({
        name: form.get("name"),
        employee_id: form.get("employee_id"),
        tag: form.get("tag"),
        role: form.get("role"),
        allowed: form.has("allowed"),
      }),
    });
    event.currentTarget.reset();
    message.textContent = "Employee added.";
    await refresh();
  } catch (error) {
    message.textContent = error.message;
  }
});

document.querySelector("#scan-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const tag = new FormData(event.currentTarget).get("tag");
  try {
    const result = await request("/api/scan", {
      method: "POST",
      body: JSON.stringify({ tag }),
    });
    document.querySelector("#scan-result").textContent = JSON.stringify(result, null, 2);
    await refresh();
  } catch (error) {
    document.querySelector("#scan-result").textContent = error.message;
  }
});

refresh();
window.setInterval(refresh, 4000);
