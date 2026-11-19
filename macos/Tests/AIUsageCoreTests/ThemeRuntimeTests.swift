@testable import AIUsageCore
import AIUsageDesign
import Foundation
import XCTest

/// The runtime path the views use: TokenColor resolved under the environment palette.
final class ThemeRuntimeTests: XCTestCase {
    private let json = ##"""
    {"version": 1, "global": "g", "providers": {"claude-az": "p"},
     "schemes": {"g": {"light": {"accent": "#111111", "panel": "#FAFAFA"}, "dark": {"accent": "#222222"}},
                 "p": {"light": {"accent": "#333333"}}}}
    """##

    private func state() throws -> ThemeState {
        ThemeState(document: try ThemeParser.parse(Data(json.utf8)).get(), fileExists: true)
    }

    private func pair(_ token: TokenColor, _ palette: ThemePalette) -> [UInt32] {
        let r = token.resolved(palette)
        return [r.light, r.dark]
    }

    func testNoThemeIsBuiltIn() {
        for token in [T.Color.accent, T.Color.text, T.Color.series2, T.Color.cardBackground, T.Color.chartInput] {
            XCTAssertEqual(pair(token, .builtIn), [token.light, token.dark], token.name)
        }
        XCTAssertEqual(T.Color.cardBackground.role, "panel")
        XCTAssertEqual(pair(T.Color.cardBackground, .builtIn), pair(T.Color.panel, .builtIn))
    }

    func testGlobalSchemeRecolorsTokensAndComponents() throws {
        let global = ThemePalette(state: try state())
        XCTAssertEqual(pair(T.Color.accent, global), [0x111111, 0x222222])
        XCTAssertEqual(pair(T.Color.chartInput, global), [0x111111, 0x222222], "component follows accent")
        XCTAssertEqual(pair(T.Color.cardBackground, global), [0xFAFAFA, T.Color.panel.dark], "dark inherits built-in")
        XCTAssertEqual(pair(T.Color.text, global), [T.Color.text.light, T.Color.text.dark])
    }

    func testProviderScopeOverridesGlobal() throws {
        let scoped = ThemePalette(state: try state(), instance: "claude-az")
        XCTAssertEqual(pair(T.Color.meterFill, scoped), [0x333333, 0x222222])
        XCTAssertEqual(pair(T.Color.cardBackground, scoped), [0xFAFAFA, T.Color.panel.dark])
        let other = ThemePalette(state: try state(), instance: "claude")
        XCTAssertEqual(pair(T.Color.accent, other), [0x111111, 0x222222])
    }

    func testUnscopedAllViewIgnoresProviderOverrides() throws {
        let unscoped = ThemePalette(state: try state())
        XCTAssertNil(unscoped.instance)
        XCTAssertEqual(pair(T.Color.chartAccount1, unscoped), [T.Color.series1.light, T.Color.series1.dark])
        XCTAssertEqual(pair(T.Color.accent, unscoped), [0x111111, 0x222222])
    }
}
