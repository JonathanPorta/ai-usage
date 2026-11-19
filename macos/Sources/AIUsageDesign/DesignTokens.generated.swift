// GENERATED FILE — do not edit.
// Source: macos/design/tokens.json
// Regenerate with `make design-build`; `make design-build-check` fails on drift.

import CoreGraphics

public enum DesignTokens {
    public enum Color {
        public static let accent = TokenColor(name: "accent", role: "accent", light: 0x2E6CBA, dark: 0x78AFF4)
        public static let accentBackground = TokenColor(name: "accentBackground", role: "accentBackground", light: 0xE8F0FB, dark: 0x26354A)
        public static let accentSoft = TokenColor(name: "accentSoft", role: "accentSoft", light: 0x5586C6, dark: 0x6C94C7)
        public static let background = TokenColor(name: "background", role: "background", light: 0xF7F7F8, dark: 0x242527)
        public static let danger = TokenColor(name: "danger", role: "danger", light: 0xB3261E, dark: 0xF2A49C)
        public static let dangerBackground = TokenColor(name: "dangerBackground", role: "dangerBackground", light: 0xFDECEA, dark: 0x45292A)
        public static let focus = TokenColor(name: "focus", role: "focus", light: 0x2E6CBA, dark: 0x8FBDF7)
        public static let hover = TokenColor(name: "hover", role: "hover", light: 0xEDF0F4, dark: 0x3B3D42)
        public static let line = TokenColor(name: "line", role: "line", light: 0xDEDFE2, dark: 0x45464B)
        public static let lineStrong = TokenColor(name: "lineStrong", role: "lineStrong", light: 0xC9CBD0, dark: 0x5A5C62)
        public static let muted = TokenColor(name: "muted", role: "muted", light: 0x5F6268, dark: 0xB1B4BC)
        public static let panel = TokenColor(name: "panel", role: "panel", light: 0xFFFFFF, dark: 0x303134)
        public static let series1 = TokenColor(name: "series1", role: "series1", light: 0x2E6CBA, dark: 0x78AFF4)
        public static let series2 = TokenColor(name: "series2", role: "series2", light: 0x2E2163, dark: 0xF3EFFF)
        public static let series3 = TokenColor(name: "series3", role: "series3", light: 0x1D8577, dark: 0x2A9A8C)
        public static let series4 = TokenColor(name: "series4", role: "series4", light: 0x454E5B, dark: 0xA9B0BA)
        public static let success = TokenColor(name: "success", role: "success", light: 0x286F3D, dark: 0x86C999)
        public static let text = TokenColor(name: "text", role: "text", light: 0x202124, dark: 0xF1F2F3)
        public static let track = TokenColor(name: "track", role: "track", light: 0xE6E8EC, dark: 0x43454A)
        public static let warning = TokenColor(name: "warning", role: "warning", light: 0x7D520F, dark: 0xE7BC70)
        public static let warningBackground = TokenColor(name: "warningBackground", role: "warningBackground", light: 0xFFF4DD, dark: 0x3E3423)
        public static let cardBackground = TokenColor(name: "cardBackground", role: "panel", light: 0xFFFFFF, dark: 0x303134)
        public static let cardBorder = TokenColor(name: "cardBorder", role: "line", light: 0xDEDFE2, dark: 0x45464B)
        public static let chartAccount1 = TokenColor(name: "chartAccount1", role: "series1", light: 0x2E6CBA, dark: 0x78AFF4)
        public static let chartAccount2 = TokenColor(name: "chartAccount2", role: "series2", light: 0x2E2163, dark: 0xF3EFFF)
        public static let chartAccount3 = TokenColor(name: "chartAccount3", role: "series3", light: 0x1D8577, dark: 0x2A9A8C)
        public static let chartAccount4 = TokenColor(name: "chartAccount4", role: "series4", light: 0x454E5B, dark: 0xA9B0BA)
        public static let chartInput = TokenColor(name: "chartInput", role: "accent", light: 0x2E6CBA, dark: 0x78AFF4)
        public static let chartNotCollected = TokenColor(name: "chartNotCollected", role: "track", light: 0xE6E8EC, dark: 0x43454A)
        public static let chartOutput = TokenColor(name: "chartOutput", role: "accentSoft", light: 0x5586C6, dark: 0x6C94C7)
        public static let chartStale = TokenColor(name: "chartStale", role: "warning", light: 0x7D520F, dark: 0xE7BC70)
        public static let chartToday = TokenColor(name: "chartToday", role: "accentBackground", light: 0xE8F0FB, dark: 0x26354A)
        public static let meterFill = TokenColor(name: "meterFill", role: "accent", light: 0x2E6CBA, dark: 0x78AFF4)
        public static let meterTrack = TokenColor(name: "meterTrack", role: "track", light: 0xE6E8EC, dark: 0x43454A)
        public static let meterWarning = TokenColor(name: "meterWarning", role: "warning", light: 0x7D520F, dark: 0xE7BC70)

        /// Built-in light/dark value of every themable role, by role name.
        public static let roleDefaults: [String: (light: UInt32, dark: UInt32)] = [
            "accent": (0x2E6CBA, 0x78AFF4),
            "accentBackground": (0xE8F0FB, 0x26354A),
            "accentSoft": (0x5586C6, 0x6C94C7),
            "background": (0xF7F7F8, 0x242527),
            "danger": (0xB3261E, 0xF2A49C),
            "dangerBackground": (0xFDECEA, 0x45292A),
            "focus": (0x2E6CBA, 0x8FBDF7),
            "hover": (0xEDF0F4, 0x3B3D42),
            "line": (0xDEDFE2, 0x45464B),
            "lineStrong": (0xC9CBD0, 0x5A5C62),
            "muted": (0x5F6268, 0xB1B4BC),
            "panel": (0xFFFFFF, 0x303134),
            "series1": (0x2E6CBA, 0x78AFF4),
            "series2": (0x2E2163, 0xF3EFFF),
            "series3": (0x1D8577, 0x2A9A8C),
            "series4": (0x454E5B, 0xA9B0BA),
            "success": (0x286F3D, 0x86C999),
            "text": (0x202124, 0xF1F2F3),
            "track": (0xE6E8EC, 0x43454A),
            "warning": (0x7D520F, 0xE7BC70),
            "warningBackground": (0xFFF4DD, 0x3E3423),
        ]
    }

    public enum FontSize {
        public static let body: CGFloat = 13
        public static let caption: CGFloat = 12
        public static let chart: CGFloat = 11
        public static let figure: CGFloat = 24
        public static let lead: CGFloat = 15
        public static let title: CGFloat = 20
    }

    public enum Space {
        public static let s1: CGFloat = 4
        public static let s2: CGFloat = 8
        public static let s3: CGFloat = 12
        public static let s4: CGFloat = 16
        public static let s5: CGFloat = 20
        public static let s6: CGFloat = 24
        public static let s8: CGFloat = 32
    }

    public enum Radius {
        public static let card: CGFloat = 10
        public static let medium: CGFloat = 8
        public static let popover: CGFloat = 15
        public static let small: CGFloat = 6
    }

    public enum Duration {
        public static let fast: Double = 150 / 1000
        public static let medium: Double = 240 / 1000
    }

    public enum Layout {
        public static let historyMinHeight: CGFloat = 460
        public static let historyMinWidth: CGFloat = 640
        public static let popoverMaxHeight: CGFloat = 780
        public static let popoverScreenMargin: CGFloat = 96
        public static let popoverWidth: CGFloat = 420
    }
}
