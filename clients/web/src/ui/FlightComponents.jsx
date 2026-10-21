import React, { useId, useRef } from "react";

function cx(...values) {
  return values.filter(Boolean).join(" ");
}

function Spinner() {
  return <span className="fdx-spinner" aria-hidden="true" />;
}

export function Button({
  children,
  variant = "primary",
  size = "md",
  loading = false,
  error = false,
  disabled = false,
  leading,
  trailing,
  className = "",
  type = "button",
  ...props
}) {
  const unavailable = disabled || loading;
  const state = loading ? "loading" : error ? "error" : disabled ? "disabled" : "default";
  return (
    <button
      type={type}
      disabled={unavailable}
      aria-busy={loading || undefined}
      data-variant={error ? "error" : variant}
      data-state={state}
      data-size={size}
      className={cx("fdx-button", className)}
      {...props}
    >
      {loading ? <Spinner /> : leading}
      <span>{loading ? "Working" : children}</span>
      {!loading && trailing}
    </button>
  );
}

export function IconButton({
  label,
  children,
  variant = "quiet",
  loading = false,
  error = false,
  disabled = false,
  className = "",
  ...props
}) {
  const state = loading ? "loading" : error ? "error" : disabled ? "disabled" : "default";
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      data-variant={error ? "error" : variant}
      data-state={state}
      className={cx("fdx-icon-button", className)}
      {...props}
    >
      {loading ? <Spinner /> : children}
    </button>
  );
}

export function StatusBadge({ children, tone = "neutral", pulse = false }) {
  return (
    <span className="fdx-status" data-tone={tone}>
      <span className={cx("fdx-status-dot", pulse && "is-pulsing")} aria-hidden="true" />
      {children}
    </span>
  );
}

export function Field({
  label,
  hint,
  error,
  loading = false,
  disabled = false,
  className = "",
  id: providedId,
  ...inputProps
}) {
  const generatedId = useId();
  const id = providedId || generatedId;
  const descriptionId = `${id}-description`;
  return (
    <label className={cx("fdx-field", className)} htmlFor={id} data-error={Boolean(error)}>
      <span className="fdx-field-label">{label}</span>
      <span className="fdx-field-control">
        <input
          id={id}
          disabled={disabled}
          aria-invalid={Boolean(error)}
          aria-busy={loading || undefined}
          aria-describedby={hint || error ? descriptionId : undefined}
          {...inputProps}
        />
        {loading && <Spinner />}
      </span>
      {(error || hint) && (
        <span id={descriptionId} className="fdx-field-message" role={error ? "alert" : undefined}>
          {error || hint}
        </span>
      )}
    </label>
  );
}

export function SelectField({
  label,
  options,
  hint,
  error,
  disabled = false,
  className = "",
  id: providedId,
  ...selectProps
}) {
  const generatedId = useId();
  const id = providedId || generatedId;
  const descriptionId = `${id}-description`;
  return (
    <label className={cx("fdx-field fdx-select-field", className)} htmlFor={id} data-error={Boolean(error)}>
      <span className="fdx-field-label">{label}</span>
      <span className="fdx-field-control">
        <select
          id={id}
          disabled={disabled}
          aria-invalid={Boolean(error)}
          aria-describedby={hint || error ? descriptionId : undefined}
          {...selectProps}
        >
          {options.map((option) => (
            <option key={option.value} value={option.value} disabled={option.disabled}>
              {option.label}
            </option>
          ))}
        </select>
        <span className="fdx-select-chevron" aria-hidden="true">⌄</span>
      </span>
      {(error || hint) && (
        <span id={descriptionId} className="fdx-field-message" role={error ? "alert" : undefined}>
          {error || hint}
        </span>
      )}
    </label>
  );
}

export function TextAreaField({
  label,
  hint,
  error,
  disabled = false,
  className = "",
  id: providedId,
  rows = 4,
  ...textAreaProps
}) {
  const generatedId = useId();
  const id = providedId || generatedId;
  const descriptionId = `${id}-description`;
  return (
    <label className={cx("fdx-field fdx-textarea-field", className)} htmlFor={id} data-error={Boolean(error)}>
      <span className="fdx-field-label">{label}</span>
      <span className="fdx-field-control">
        <textarea
          id={id}
          rows={rows}
          disabled={disabled}
          aria-invalid={Boolean(error)}
          aria-describedby={hint || error ? descriptionId : undefined}
          {...textAreaProps}
        />
      </span>
      {(error || hint) && (
        <span id={descriptionId} className="fdx-field-message" role={error ? "alert" : undefined}>
          {error || hint}
        </span>
      )}
    </label>
  );
}

export function Toggle({
  label,
  description,
  checked,
  onChange,
  disabled = false,
  className = "",
  ...props
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      data-checked={checked}
      className={cx("fdx-toggle", className)}
      onClick={() => onChange?.(!checked)}
      {...props}
    >
      <span className="fdx-toggle-track" aria-hidden="true"><i /></span>
      <span className="fdx-toggle-copy">
        <strong>{label}</strong>
        {description && <small>{description}</small>}
      </span>
    </button>
  );
}

export function CheckField({
  label,
  description,
  className = "",
  disabled = false,
  ...inputProps
}) {
  return (
    <label className={cx("fdx-check", className)} data-disabled={disabled}>
      <input type="checkbox" disabled={disabled} {...inputProps} />
      <span className="fdx-check-mark" aria-hidden="true" />
      <span className="fdx-check-copy">
        <strong>{label}</strong>
        {description && <small>{description}</small>}
      </span>
    </label>
  );
}

export function SegmentedControl({ label, items, value, onChange, disabled = false }) {
  const refs = useRef([]);
  const move = (event, index) => {
    const keys = ["ArrowRight", "ArrowLeft", "Home", "End"];
    if (!keys.includes(event.key)) return;
    event.preventDefault();
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % items.length;
    if (event.key === "ArrowLeft") next = (index - 1 + items.length) % items.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = items.length - 1;
    refs.current[next]?.focus();
    onChange(items[next].value);
  };
  return (
    <div className="fdx-segment" role="group" aria-label={label} aria-disabled={disabled || undefined}>
      {items.map((item, index) => (
        <button
          key={item.value}
          ref={(node) => { refs.current[index] = node; }}
          type="button"
          aria-pressed={value === item.value}
          disabled={disabled}
          onClick={() => onChange(item.value)}
          onKeyDown={(event) => move(event, index)}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

export function Tabs({ label, items, value, onChange, className = "" }) {
  const refs = useRef([]);
  const generatedId = useId().replace(/:/g, "");
  const selected = items.find((item) => item.value === value) || items.find((item) => !item.disabled);

  const move = (event, index) => {
    if (!["ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const enabledIndexes = items
      .map((item, itemIndex) => (!item.disabled ? itemIndex : -1))
      .filter((itemIndex) => itemIndex >= 0);
    const currentPosition = enabledIndexes.indexOf(index);
    let nextIndex = index;
    if (event.key === "Home") nextIndex = enabledIndexes[0];
    if (event.key === "End") nextIndex = enabledIndexes[enabledIndexes.length - 1];
    if (event.key === "ArrowRight") nextIndex = enabledIndexes[(currentPosition + 1) % enabledIndexes.length];
    if (event.key === "ArrowLeft") nextIndex = enabledIndexes[(currentPosition - 1 + enabledIndexes.length) % enabledIndexes.length];
    refs.current[nextIndex]?.focus();
    onChange(items[nextIndex].value);
  };

  return (
    <div className={cx("fdx-tabs", className)}>
      <div className="fdx-tab-list" role="tablist" aria-label={label}>
        {items.map((item, index) => {
          const active = item.value === selected?.value;
          return (
            <button
              key={item.value}
              ref={(node) => { refs.current[index] = node; }}
              id={`${generatedId}-tab-${item.value}`}
              type="button"
              role="tab"
              aria-selected={active}
              aria-controls={`${generatedId}-panel-${item.value}`}
              tabIndex={active ? 0 : -1}
              disabled={item.disabled}
              onClick={() => onChange(item.value)}
              onKeyDown={(event) => move(event, index)}
            >
              <span>{item.label}</span>
              {item.meta && <small>{item.meta}</small>}
            </button>
          );
        })}
      </div>
      {selected && (
        <div
          id={`${generatedId}-panel-${selected.value}`}
          role="tabpanel"
          aria-labelledby={`${generatedId}-tab-${selected.value}`}
          className="fdx-tab-panel"
        >
          {selected.content}
        </div>
      )}
    </div>
  );
}

export function ProgressBar({
  label,
  value,
  max = 100,
  valueLabel,
  detail,
  tone = "signal",
  className = "",
}) {
  const displayValue = valueLabel || `${Math.round((value / max) * 100)}%`;
  return (
    <div className={cx("fdx-progress", className)} data-tone={tone}>
      <div className="fdx-progress-meta">
        <span>{label}</span>
        <strong>{displayValue}</strong>
      </div>
      <progress value={value} max={max} aria-label={label}>{displayValue}</progress>
      {detail && <small>{detail}</small>}
    </div>
  );
}

export function DataList({ items, label = "System data", className = "" }) {
  return (
    <dl className={cx("fdx-data-list", className)} aria-label={label}>
      {items.map((item) => (
        <div key={item.label}>
          <dt>{item.label}</dt>
          <dd>
            <strong>{item.value}</strong>
            {item.detail && <small>{item.detail}</small>}
          </dd>
          {item.tone && <StatusBadge tone={item.tone}>{item.status || item.tone}</StatusBadge>}
        </div>
      ))}
    </dl>
  );
}

export function SurfaceCard({
  eyebrow,
  title,
  children,
  footer,
  interactive = false,
  loading = false,
  error = false,
  disabled = false,
  className = "",
  onClick,
}) {
  const content = (
    <>
      <div className="fdx-card-head">
        {eyebrow && <span className="fdx-eyebrow">{eyebrow}</span>}
        <StatusBadge tone={error ? "critical" : loading ? "warning" : "neutral"}>
          {error ? "Needs attention" : loading ? "Loading" : "Ready"}
        </StatusBadge>
      </div>
      <h3>{title}</h3>
      <div className="fdx-card-body">{loading ? <CardSkeleton /> : children}</div>
      {footer && <div className="fdx-card-footer">{footer}</div>}
    </>
  );

  if (interactive) {
    return (
      <button
        type="button"
        disabled={disabled || loading}
        aria-busy={loading || undefined}
        data-state={error ? "error" : loading ? "loading" : disabled ? "disabled" : "default"}
        className={cx("fdx-card fdx-card-button", className)}
        onClick={onClick}
      >
        {content}
      </button>
    );
  }
  return (
    <section
      aria-busy={loading || undefined}
      data-state={error ? "error" : loading ? "loading" : disabled ? "disabled" : "default"}
      className={cx("fdx-card", className)}
    >
      {content}
    </section>
  );
}

function CardSkeleton() {
  return (
    <span className="fdx-skeleton-stack" aria-label="Loading content">
      <span />
      <span />
      <span />
    </span>
  );
}

export function MetricStrip({ items, label = "Operational metrics" }) {
  return (
    <dl className="fdx-metric-strip" aria-label={label}>
      {items.map((item) => (
        <div key={item.label}>
          <dt>{item.label}</dt>
          <dd>{item.value}</dd>
          {item.detail && <span>{item.detail}</span>}
        </div>
      ))}
    </dl>
  );
}

export function Notice({ title, children, tone = "info", action }) {
  return (
    <aside className="fdx-notice" data-tone={tone} role={tone === "critical" ? "alert" : "status"}>
      <span className="fdx-notice-mark" aria-hidden="true" />
      <div>
        <strong>{title}</strong>
        <p>{children}</p>
      </div>
      {action && <div className="fdx-notice-action">{action}</div>}
    </aside>
  );
}

export function EmptyState({ title, children, action, loading = false, error = false }) {
  return (
    <div className="fdx-empty" aria-busy={loading || undefined} data-state={error ? "error" : loading ? "loading" : "default"}>
      <div className="fdx-empty-signal" aria-hidden="true">
        <i />
        <i />
        <i />
      </div>
      <h3>{loading ? "Preparing the view" : title}</h3>
      <p>{loading ? "This should only take a moment." : children}</p>
      {!loading && action}
    </div>
  );
}

export function HorizontalAccordion({ items, value, onChange }) {
  const refs = useRef([]);
  const move = (event, index) => {
    if (!["ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % items.length;
    if (event.key === "ArrowLeft") next = (index - 1 + items.length) % items.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = items.length - 1;
    refs.current[next]?.focus();
    onChange(items[next].id);
  };
  return (
    <div className="fdx-accordion">
      {items.map((item, index) => {
        const open = item.id === value;
        return (
          <section key={item.id} className="fdx-accordion-item" data-open={open}>
            <button
              ref={(node) => { refs.current[index] = node; }}
              type="button"
              aria-expanded={open}
              aria-controls={`accordion-${item.id}`}
              onClick={() => onChange(item.id)}
              onKeyDown={(event) => move(event, index)}
            >
              <span>{item.label}</span>
              <small>{item.short}</small>
            </button>
            <div id={`accordion-${item.id}`} hidden={!open}>
              <strong>{item.title}</strong>
              <p>{item.body}</p>
            </div>
          </section>
        );
      })}
    </div>
  );
}

export function TokenMarquee({ items }) {
  const row = items.map((item) => (
    <span key={item}>
      <i aria-hidden="true" />
      {item}
    </span>
  ));
  return (
    <div className="fdx-marquee" aria-label={`System tokens: ${items.join(", ")}`}>
      <div>{row}</div>
      <div aria-hidden="true">{row}</div>
    </div>
  );
}
