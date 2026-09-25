import type { Metadata } from "next";
import "./globals.css";

// System fonts only (offline venue, CLAUDE.md §16): no next/font/google, no CDNs.
export const metadata: Metadata = {
  title: "VERDICT",
  description: "Validation-guided reconstruction of fragmented files from damaged storage",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full bg-zinc-950 text-zinc-100">{children}</body>
    </html>
  );
}
