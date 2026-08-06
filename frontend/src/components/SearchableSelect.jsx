import { useEffect, useRef, useState } from "react";

/**
 * A text-searchable dropdown: type to substring-filter options (matches
 * anywhere in the label, not just the start), click or Enter to pick.
 * Options: [{ value, label }]. value/onChange behave like a native select,
 * with "" representing "nothing selected".
 */
export default function SearchableSelect({ options, value, onChange, placeholder = "Any" }) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const containerRef = useRef(null);

  const selected = options.find((o) => String(o.value) === String(value));

  useEffect(() => {
    if (!open) setQuery(selected ? selected.label : "");
  }, [selected, open]);

  useEffect(() => {
    function handleClickOutside(e) {
      if (containerRef.current && !containerRef.current.contains(e.target)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const needle = query.trim().toLowerCase();
  const filtered = needle
    ? options.filter((o) => o.label.toLowerCase().includes(needle))
    : options;

  const pick = (val) => {
    onChange(val);
    setOpen(false);
  };

  const handleKeyDown = (e) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      setHighlight((h) => Math.min(h + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => Math.max(h - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (filtered[highlight]) pick(String(filtered[highlight].value));
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <div className="searchable-select" ref={containerRef}>
      <input
        type="text"
        className="searchable-select__input"
        value={open ? query : selected ? selected.label : ""}
        placeholder={placeholder}
        onFocus={() => {
          setOpen(true);
          setQuery("");
          setHighlight(0);
        }}
        onChange={(e) => {
          setQuery(e.target.value);
          setHighlight(0);
          setOpen(true);
        }}
        onKeyDown={handleKeyDown}
      />
      {open && (
        <ul className="searchable-select__list">
          <li
            className="searchable-select__option searchable-select__option--any"
            onMouseDown={() => pick("")}
          >
            {placeholder}
          </li>
          {filtered.length === 0 && <li className="searchable-select__empty">No matches</li>}
          {filtered.map((o, i) => (
            <li
              key={o.value}
              className={`searchable-select__option ${i === highlight ? "searchable-select__option--highlight" : ""}`}
              onMouseDown={() => pick(String(o.value))}
              onMouseEnter={() => setHighlight(i)}
            >
              {o.label}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
