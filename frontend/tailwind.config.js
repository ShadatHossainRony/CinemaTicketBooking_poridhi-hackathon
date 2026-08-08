/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#0b1020",
        panel: "#131a2e",
        accent: "#e50914",
        muted: "#5b6478",
        text: "#e6e8ef",
      },
    },
  },
  plugins: [],
};
