import { useEffect, useMemo, useState } from "react";
import { getSpecialist } from "../api.js";

const TONE_LABEL = { thiqa: "ثقة أو صحابي", saduq: "صدوق", majhul: "مجهول أو مستور", weak: "ضعيف", unknown: "غير مصنّف أو بلا درجة" };

const shortName = (name) => name.split(/[،:]/)[0].split(/\s+/).slice(0, 5).join(" ");

/* الأسانيد مخزّنة من الصحابي إلى صاحب الكتاب؛ تُعرض للقراءة من صاحب الكتاب نزولاً كما في كتب الحديث. */
function Chain({ route }) {
  const [open, setOpen] = useState(null);
  const people = [...route.chain].reverse();
  return (
    <div className="chain">
      <ol className="chain-people">
        {people.map((p, i) => (
          <li key={`${p.id}-${i}`}>
            <button
              type="button"
              className={`narrator tone-n-${p.tone}`}
              aria-expanded={open === i}
              onClick={() => setOpen(open === i ? null : i)}
              title={p.grade || "غير مصنّف"}
            >
              <span className="dot" aria-hidden="true" />
              {shortName(p.name)}
            </button>
          </li>
        ))}
      </ol>
      {open !== null && (
        <div className="narrator-detail">
          <strong>{people[open].name}</strong>
          <span>{people[open].grade || "لا درجة مسجّلة في القاعدة"}</span>
        </div>
      )}
      {route.weak_positions.length > 0 && (
        <p className="chain-flag">
          فيه من ضُعّف: {route.weak_positions.map((i) => shortName(route.chain[i].name)).join("، ")}
        </p>
      )}
      {route.majhul_positions?.length > 0 && (
        <p className="chain-flag majhul">
          فيه مجهول أو مستور: {route.majhul_positions.map((i) => shortName(route.chain[i].name)).join("، ")}
        </p>
      )}
    </div>
  );
}

function buildTree(routes) {
  const root = { children: new Map(), count: 0 };
  for (const r of routes) {
    let level = root;
    root.count += 1;
    r.chain.forEach((p, idx) => {
      if (!level.children.has(p.id)) level.children.set(p.id, { person: p, children: new Map(), count: 0, books: new Set() });
      const node = level.children.get(p.id);
      node.count += 1;
      if (idx === r.chain.length - 1) node.books.add(`${r.book} ${r.number}`);
      level = node;
    });
  }
  // المدار: الراوي الذي تتفرّع عنه أكثر الطرق (أكبر عدد طرق، والأعمق عند التساوي)
  let madar = null;
  const walk = (node, depth) => {
    for (const child of node.children.values()) {
      if (child.children.size >= 2 && child.count >= 2) {
        if (!madar || child.count > madar.count || (child.count === madar.count && depth > madar.depth)) {
          madar = { person: child.person, count: child.count, depth };
        }
      }
      walk(child, depth + 1);
    }
  };
  walk(root, 0);
  return { root, madar, total: root.count };
}

function TreeNode({ node }) {
  const [collapsed, setCollapsed] = useState(false);
  const kids = [...node.children.values()];
  return (
    <li>
      <div className="tree-row">
        {kids.length > 0 ? (
          <button type="button" className="tree-toggle" onClick={() => setCollapsed(!collapsed)} aria-label={collapsed ? "توسيع" : "طي"}>
            {collapsed ? "+" : "−"}
          </button>
        ) : (
          <span className="tree-toggle" />
        )}
        <span className={`dot tone-n-${node.person.tone}`} aria-hidden="true" />
        <span title={node.person.grade || "غير مصنّف"}>{shortName(node.person.name)}</span>
        {kids.length > 1 && <span className="muted small">({kids.length} طرق)</span>}
        {[...node.books].map((b) => (
          <span key={b} className="book-tag">{b}</span>
        ))}
      </div>
      {kids.length > 0 && !collapsed && (
        <ul className="tree-children">
          {kids.map((k) => (
            <TreeNode key={k.person.id} node={k} />
          ))}
        </ul>
      )}
    </li>
  );
}

export default function IsnadPanel({ textId }) {
  const [state, setState] = useState({ status: "loading" });
  const [tab, setTab] = useState("routes");

  useEffect(() => {
    let alive = true;
    setState({ status: "loading" });
    getSpecialist(textId)
      .then((data) => alive && setState({ status: "done", data }))
      .catch((err) => alive && setState({ status: "error", message: err.message }));
    return () => {
      alive = false;
    };
  }, [textId]);

  const tree = useMemo(() => (state.data ? buildTree(state.data.routes) : null), [state.data]);

  if (state.status === "loading") return <p className="muted small">جارٍ تحميل الأسانيد…</p>;
  if (state.status === "error") return <p className="error-inline">{state.message}</p>;

  const { hadith, routes, narrators_loaded, truncated, note } = state.data;
  const byBook = routes.reduce((acc, r) => {
    const key = `${r.book}|${r.number}|${r.text_id}`;
    (acc[key] ||= []).push(r);
    return acc;
  }, {});

  return (
    <section className="isnad-panel" aria-label="الأسانيد ودرجات الرواة">
      <h4>النص بإسناده: {hadith.book}، رقم {hadith.number}</h4>
      <p className="full-text">{hadith.full_text}</p>

      {!narrators_loaded && (
        <p className="notice">لم تُستورد بيانات الرواة بعد، فتظهر الأسماء معرّفات. شغّل أمر import_isnad_data.</p>
      )}

      <div className="tabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === "routes"} onClick={() => setTab("routes")}>
          الطرق ({routes.length})
        </button>
        <button type="button" role="tab" aria-selected={tab === "tree"} onClick={() => setTab("tree")}>
          شجرة الإسناد
        </button>
      </div>

      {tab === "routes" && (
        <div className="routes">
          {Object.entries(byBook).map(([key, rs]) => (
            <div key={key} className="route-group">
              <h5>
                {rs[0].book}، رقم {rs[0].number}
                {rs[0].is_self && <span className="book-tag">هذا الموضع</span>}
                {!rs[0].has_matn && <span className="book-tag">متابعة بلا متن مستقل</span>}
              </h5>
              {rs.map((r, i) => (
                <Chain key={i} route={r} />
              ))}
            </div>
          ))}
          {routes.length === 0 && <p className="muted small">لا أسانيد مسجّلة لهذا الحديث في القاعدة.</p>}
          {truncated && <p className="muted small">عُرضت أول 150 موضعاً من التخريج.</p>}
        </div>
      )}

      {tab === "tree" && tree && (
        <div className="tree">
          {tree.madar && (
            <p className="madar">
              مدار أكثر الطرق: <strong>{shortName(tree.madar.person.name)}</strong>
              <span className="muted">
                {" "}({tree.madar.person.grade || "غير مصنّف"})، تمرّ به {tree.madar.count} من {tree.total} طريقاً
              </span>
            </p>
          )}
          <p className="muted small">من الصحابي إلى أصحاب الكتب، وتتفرّع الشجرة حيث تختلف الطرق.</p>
          <ul className="tree-root">
            {[...tree.root.children.values()].map((n) => (
              <TreeNode key={n.person.id} node={n} />
            ))}
          </ul>
        </div>
      )}

      <div className="legend">
        {Object.entries(TONE_LABEL).map(([k, v]) => (
          <span key={k}>
            <span className={`dot tone-n-${k}`} aria-hidden="true" /> {v}
          </span>
        ))}
      </div>
      <p className="muted small">{note}</p>
    </section>
  );
}
