import type { ButtonHTMLAttributes } from "react";

export function Button({
  variant = "outline",
  size = "sm",
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "outline" | "default";
  size?: "sm" | "default";
}) {
  const classes = ["app-button", `app-button--${variant}`, size === "sm" ? "app-button--sm" : "", className]
    .filter(Boolean)
    .join(" ");
  return <button type="button" className={classes} {...props} />;
}
