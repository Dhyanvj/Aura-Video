/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Defined as CSS variables (see src/index.css) that flip between
        // :root (light) and .dark - every existing bg-canvas/bg-panel/
        // border-border usage across the app gets both themes for free,
        // with no per-component changes needed.
        canvas: "var(--color-canvas)",
        panel: "var(--color-panel)",
        panel2: "var(--color-panel2)",
        border: "var(--color-border)",
        accent: "var(--color-accent)",
      },
    },
  },
  plugins: [],
};
