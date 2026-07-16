/** @type {import('tailwindcss').Config} */
// Build-time Tailwind — mirrors the exact theme that used to live inline in index.html
// (Play CDN `tailwind.config = {...}`). Compiling at build time ships the utilities inside
// our bundled CSS, so there is no runtime CDN download: no cold-start flash of unstyled
// content, and the app still renders if a captain's network blocks cdn.tailwindcss.com.
// Token NAMES are unchanged so every component reskins in place.
import forms from "@tailwindcss/forms";
import containerQueries from "@tailwindcss/container-queries";

export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{js,jsx,ts,tsx}"],
  theme: {
    extend: {
      colors: {
        /* Palette: Stitch "Valmo Advocate Command Center" — Trust Blue accent, Partner Green,
           charcoal-navy surfaces. */
        "surface-variant": "#2d3449", "on-primary-container": "#8c909f",
        "on-primary-fixed-variant": "#424754", "on-surface-variant": "#c2c6d6",
        "secondary": "#adc6ff", "surface": "#0b1326", "surface-container-high": "#222a3d",
        "surface-container": "#171f33", "secondary-container": "#4d8eff",
        "on-background": "#dae2fd", "surface-container-low": "#131b2e",
        "background": "#0b1326", "surface-bright": "#31394d", "outline-variant": "#424754",
        "secondary-fixed": "#d8e2ff", "primary": "#adc6ff", "error": "#ffb4ab",
        "surface-container-lowest": "#060e20", "on-surface": "#dae2fd",
        "surface-container-highest": "#2d3449", "surface-dim": "#0b1326",
        "tertiary": "#4edea3", "on-tertiary": "#002113", "on-secondary": "#002e6a",
        "outline": "#8c909f", "warn": "#ffb95f",
      },
      fontFamily: {
        "body-base": ["Inter"], "headline-md": ["Inter"], "display-lg": ["Inter"],
        "data-mono": ["JetBrains Mono"], "headline-sm": ["Inter"], "label-caps": ["Inter"],
      },
      spacing: { unit: "4px", lg: "24px", sm: "8px", gutter: "20px", md: "16px", xs: "4px", xl: "48px" },
      borderRadius: { DEFAULT: "0.125rem", lg: "0.25rem", xl: "0.5rem", full: "0.75rem" },
    },
  },
  plugins: [forms, containerQueries],
};
