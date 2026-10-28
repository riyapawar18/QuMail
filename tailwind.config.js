module.exports = {
  content: ["./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        background: "#DCDCDC",
        nav: "#A9A9A9",
        active: "#C9D4E8",
        separator: "#CFCFCF",
        primary: "#1A73E8",
      },
      fontFamily: {
        sans: ["Inter", "sans-serif"],
      },
      borderRadius: {
        'btn': '16px',
        'compose': '24px',
      }
    },
  },
  plugins: [],
};