/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "#0b0d12",
        panel: "#12151c",
        panel2: "#181c26",
        border: "#242a36",
        accent: "#6366f1",
      },
    },
  },
  plugins: [],
};
