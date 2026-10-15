// GENERATED FILE — do not edit.
// Source: macos/design/tokens.json
// Regenerate with `make design-build`; `make design-build-check` fails on drift.

import CoreGraphics

public enum DesignTokens {
    public enum Color {
        public static let accent = TokenColor(light: 0x2E6CBA, dark: 0x78AFF4)
        public static let accentBackground = TokenColor(light: 0xE8F0FB, dark: 0x26354A)
        public static let accentSoft = TokenColor(light: 0x5586C6, dark: 0x6C94C7)
        public static let background = TokenColor(light: 0xF7F7F8, dark: 0x242527)
        public static let danger = TokenColor(light: 0xB3261E, dark: 0xF2A49C)
        public static let dangerBackground = TokenColor(light: 0xFDECEA, dark: 0x45292A)
        public static let focus = TokenColor(light: 0x2E6CBA, dark: 0x8FBDF7)
        public static let hover = TokenColor(light: 0xEDF0F4, dark: 0x3B3D42)
        public static let line = TokenColor(light: 0xDEDFE2, dark: 0x45464B)
        public static let lineStrong = TokenColor(light: 0xC9CBD0, dark: 0x5A5C62)
        public static let muted = TokenColor(light: 0x5F6268, dark: 0xB1B4BC)
        public static let panel = TokenColor(light: 0xFFFFFF, dark: 0x303134)
        public static let success = TokenColor(light: 0x286F3D, dark: 0x86C999)
        public static let text = TokenColor(light: 0x202124, dark: 0xF1F2F3)
        public static let track = TokenColor(light: 0xE6E8EC, dark: 0x43454A)
        public static let warning = TokenColor(light: 0x7D520F, dark: 0xE7BC70)
        public static let warningBackground = TokenColor(light: 0xFFF4DD, dark: 0x3E3423)
        public static let cardBackground = TokenColor(light: 0xFFFFFF, dark: 0x303134)
        public static let cardBorder = TokenColor(light: 0xDEDFE2, dark: 0x45464B)
        public static let chartInput = TokenColor(light: 0x2E6CBA, dark: 0x78AFF4)
        public static let chartNotCollected = TokenColor(light: 0xE6E8EC, dark: 0x43454A)
        public static let chartOutput = TokenColor(light: 0x5586C6, dark: 0x6C94C7)
        public static let chartStale = TokenColor(light: 0x7D520F, dark: 0xE7BC70)
        public static let chartToday = TokenColor(light: 0xE8F0FB, dark: 0x26354A)
        public static let meterFill = TokenColor(light: 0x2E6CBA, dark: 0x78AFF4)
        public static let meterTrack = TokenColor(light: 0xE6E8EC, dark: 0x43454A)
        public static let meterWarning = TokenColor(light: 0x7D520F, dark: 0xE7BC70)
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
