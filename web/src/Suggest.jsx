import { useState } from "react";

// A text input with a dropdown of suggestions. Keyboard: ↑/↓ to move, Enter to pick, Esc to close.
export default function Suggest({ value, onChange, items, onPick, renderItem, placeholder, loading, empty, onBlur }) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const show = open && (items.length > 0 || loading || (empty && value.trim().length >= 3));

  function pick(item) {
    onPick(item);
    setOpen(false);
    setActive(0);
  }

  function onKeyDown(e) {
    if (!open && (e.key === "ArrowDown" || e.key === "ArrowUp")) setOpen(true);
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(a + 1, items.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (e.key === "Enter") {
      if (items[active]) {
        e.preventDefault();
        pick(items[active]);
      }
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  }

  return (
    <div className="suggest">
      <input
        value={value}
        placeholder={placeholder}
        onChange={(e) => {
          onChange(e.target.value);
          setOpen(true);
          setActive(0);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => {
          setOpen(false);
          onBlur?.();
        }}
        onKeyDown={onKeyDown}
      />
      {show && (
        <ul className="suggest-list">
          {loading && items.length === 0 && <li className="muted">Searching…</li>}
          {!loading && items.length === 0 && empty && <li className="muted">{empty}</li>}
          {items.map((item, i) => (
            <li
              key={i}
              className={i === active ? "active" : ""}
              // mousedown fires before the input's blur, so the click isn't lost
              onMouseDown={(e) => {
                e.preventDefault();
                pick(item);
              }}
              onMouseEnter={() => setActive(i)}
            >
              {renderItem(item)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
