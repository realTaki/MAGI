export function Avatar({
  color,
  size = 38,
  className,
}: {
  color: string;
  size?: number;
  className?: string;
}) {
  return (
    <span
      aria-hidden="true"
      className={`demo-avatar${className ? ` ${className}` : ""}`}
      style={{
        width: size,
        height: size,
        background: color,
        fontSize: Math.round(size * 0.36),
      }}
    />
  );
}
