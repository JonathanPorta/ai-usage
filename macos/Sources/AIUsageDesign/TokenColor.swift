import AIUsageCore
import AppKit
import SwiftUI

/// A named light/dark color pair from the canonical token source.
/// `role` is the semantic token it follows when themed (a component token such as
/// `cardBackground` follows `panel`), so a theme recolors components with their role.
public struct TokenColor: Sendable {
    public let name: String
    public let role: String
    public let light: UInt32
    public let dark: UInt32

    public init(name: String, role: String, light: UInt32, dark: UInt32) {
        self.name = name
        self.role = role
        self.light = light
        self.dark = dark
    }

    /// The built-in pair, resolved against the effective appearance (never themed).
    public var nsColor: NSColor { Self.nsColor(light: light, dark: dark) }

    /// The light/dark values in effect under a palette (built-in where it sets nothing).
    public func resolved(_ palette: ThemePalette) -> (light: UInt32, dark: UInt32) {
        let override = palette.roles[role]
        return (override?.light ?? light, override?.dark ?? dark)
    }

    /// The themed color: resolved where it is drawn, from the theme scope in the
    /// SwiftUI environment (built-in -> global scheme -> provider scheme).
    public var color: ThemedColor { ThemedColor(token: self) }

    static func nsColor(light: UInt32, dark: UInt32) -> NSColor {
        NSColor(name: nil) { appearance in
            let isDark = appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
            return NSColor(rgb: isDark ? dark : light)
        }
    }
}

/// A token color as a shape style. It resolves in the environment of the view that
/// draws it, which is how a provider's scope recolors its card, detail and series
/// without any call site naming a theme. With no theme it resolves to exactly the
/// built-in color.
public struct ThemedColor: ShapeStyle, Sendable {
    let token: TokenColor?
    let alpha: Double

    init(token: TokenColor?, alpha: Double = 1) {
        self.token = token
        self.alpha = alpha
    }

    public static let clear = ThemedColor(token: nil)

    public func opacity(_ value: Double) -> ThemedColor { ThemedColor(token: token, alpha: alpha * value) }

    public func resolve(in environment: EnvironmentValues) -> Color {
        color(environment.themePalette)
    }

    /// The plain SwiftUI color under a palette (for APIs that need a `Color`).
    public func color(_ palette: ThemePalette) -> Color {
        guard let token else { return .clear }
        let base: Color
        if palette.roles[token.role] != nil {
            let pair = token.resolved(palette)
            base = Color(nsColor: TokenColor.nsColor(light: pair.light, dark: pair.dark))
        } else {
            base = Color(nsColor: token.nsColor)
        }
        return alpha == 1 ? base : base.opacity(alpha)
    }
}

/// The roles in effect for the views below: global scheme, plus a provider's own scheme
/// inside that provider's scope. `state` stays whole so a scope can be derived anywhere.
public struct ThemePalette: Equatable, Sendable {
    public var state: ThemeState
    public var instance: String?
    public var roles: [String: ThemeColor]

    public init(state: ThemeState = .builtIn, instance: String? = nil) {
        self.state = state
        self.instance = instance
        roles = state.roles(for: instance)
    }

    public static let builtIn = ThemePalette()
}

private struct ThemePaletteKey: EnvironmentKey {
    static let defaultValue = ThemePalette.builtIn
}

public extension EnvironmentValues {
    var themePalette: ThemePalette {
        get { self[ThemePaletteKey.self] }
        set { self[ThemePaletteKey.self] = newValue }
    }
}

public extension View {
    /// Applies a theme state: the global scheme to everything below.
    func theme(_ state: ThemeState) -> some View {
        environment(\.themePalette, ThemePalette(state: state))
    }

    /// Recolors the views below with one provider instance's scheme (over the global one).
    func themeScope(_ instance: String?) -> some View {
        transformEnvironment(\.themePalette) { palette in
            if palette.instance != instance { palette = ThemePalette(state: palette.state, instance: instance) }
        }
    }
}

extension NSColor {
    convenience init(rgb: UInt32) {
        self.init(
            srgbRed: CGFloat((rgb >> 16) & 0xFF) / 255,
            green: CGFloat((rgb >> 8) & 0xFF) / 255,
            blue: CGFloat(rgb & 0xFF) / 255,
            alpha: 1
        )
    }
}
