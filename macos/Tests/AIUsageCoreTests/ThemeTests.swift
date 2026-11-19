@testable import AIUsageCore
import Foundation
import XCTest

final class ThemeTests: XCTestCase {
    private var folder: URL!

    override func setUpWithError() throws {
        folder = FileManager.default.temporaryDirectory.appendingPathComponent("ai-usage-theme-tests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: folder) }

    private var file: URL { folder.appendingPathComponent(ThemeLoader.fileName) }
    private func write(_ text: String) throws { try Data(text.utf8).write(to: file) }

    private func parse(_ text: String) -> Result<ThemeDocument, ThemeError> { ThemeParser.parse(Data(text.utf8)) }

    private let valid = """
    {"version": 1, "global": "slate",
     "providers": {"claude": "warm", "claude-az": "violet", "gone": "warm"},
     "schemes": {
       "slate": {"light": {"accent": "#1769AA", "panel": "#FBFCFD"}, "dark": {"accent": "#80B7F7"}},
       "warm": {"light": {"accent": "#9A4A24"}, "dark": {"accent": "#EAAF86", "text": "#FFEEDD"}},
       "violet": {"light": {"accent": "#7245b8"}}
     }}
    """

    func testParsesValidFile() throws {
        let doc = try parse(valid).get()
        XCTAssertEqual(doc.global, "slate")
        XCTAssertEqual(doc.providers["claude-az"], "violet")
        XCTAssertEqual(doc.schemes["violet"]?.roles["accent"]?.light, 0x7245B8)
    }

    func testInheritanceOrderBuiltInGlobalProvider() throws {
        let doc = try parse(valid).get()
        let chrome = doc.roles(for: nil)
        XCTAssertEqual(chrome["accent"], ThemeColor(light: 0x1769AA, dark: 0x80B7F7))
        let claude = doc.roles(for: "claude")
        XCTAssertEqual(claude["accent"], ThemeColor(light: 0x9A4A24, dark: 0xEAAF86))
        XCTAssertEqual(claude["panel"], ThemeColor(light: 0xFBFCFD, dark: nil), "inherits global; dark falls to built-in")
        XCTAssertEqual(claude["text"], ThemeColor(light: nil, dark: 0xFFEEDD))
        XCTAssertNil(chrome["text"], "absent role = built-in")
    }

    func testPartialSchemeLightOnlyAppliesToDark() throws {
        let doc = try parse(valid).get()
        // No dark object: dark inherits from the layer below, never copies light.
        XCTAssertEqual(doc.schemes["violet"]?.roles["accent"], ThemeColor(light: 0x7245B8, dark: nil))
        XCTAssertEqual(doc.roles(for: "claude-az")["accent"], ThemeColor(light: 0x7245B8, dark: 0x80B7F7),
                       "provider light over global; dark from global")
        // A scheme with a dark section keeps built-in dark for roles it omits there.
        XCTAssertEqual(doc.schemes["slate"]?.roles["panel"], ThemeColor(light: 0xFBFCFD, dark: nil))
    }

    func testTwoClaudeAccountsTargetedSeparately() throws {
        let doc = try parse(valid).get()
        XCTAssertNotEqual(doc.roles(for: "claude")["accent"], doc.roles(for: "claude-az")["accent"])
        XCTAssertEqual(doc.roles(for: "claude-az")["accent"]?.light, 0x7245B8)
    }

    func testUnknownProviderIdsAreKeptAndHarmless() throws {
        let doc = try parse(valid).get()
        XCTAssertEqual(doc.providers["gone"], "warm")
        XCTAssertEqual(doc.roles(for: "never-assigned"), doc.roles(for: nil))
    }

    func testValidationErrorsNameWhatAndWhere() {
        func error(_ text: String) -> String? {
            if case let .failure(e) = parse(text) { return e.description }
            return nil
        }
        XCTAssertEqual(error(#"{"version": 2}"#), "version: must be 1")
        XCTAssertEqual(error(#"{"global": "x"}"#), "version: must be 1")
        XCTAssertEqual(error(#"{"version": true}"#), "version: must be 1")
        XCTAssertEqual(error(##"{"version": 1, "schemes": {"a": {"light": {"accent": "blue"}}}}"##),
                       ##"schemes.a.light.accent: "blue" is not a "#RRGGBB" color"##)
        XCTAssertNotNil(error(##"{"version": 1, "schemes": {"a": {"light": {"accent": "#12345"}}}}"##))
        XCTAssertEqual(error(##"{"version": 1, "schemes": {"a": {"dark": {"cardColor": "#123456"}}}}"##),
                       "schemes.a.dark.cardColor: unknown role; use a role listed in docs/THEMES.md")
        XCTAssertEqual(error(#"{"version": 1, "global": "nope", "schemes": {}}"#),
                       #"global: scheme "nope" is not defined in schemes"#)
        XCTAssertEqual(error(#"{"version": 1, "providers": {"claude": "nope"}}"#),
                       #"providers.claude: scheme "nope" is not defined in schemes"#)
        XCTAssertTrue(error("{nope")?.hasPrefix("not valid JSON") ?? false)
        XCTAssertNotNil(error(#"{"version": 1, "extra": 1}"#))
        XCTAssertEqual(error(String(repeating: " ", count: ThemeParser.maxBytes + 1)), "file is 64 KB; the limit is 64 KB")
    }

    func testNotesAreIgnoredAndStarterIsValidAndInert() throws {
        let doc = try ThemeParser.parse(Data(ThemeLoader.starter.utf8)).get()
        XCTAssertNil(doc.global)
        XCTAssertTrue(doc.providers.isEmpty)
        XCTAssertEqual(Set(doc.schemes.keys), ["slate", "warm", "violet"])
    }

    func testNoFileIsBuiltIn() {
        let store = ThemeStore(directory: folder)
        XCTAssertEqual(store.state, ThemeState())
        XCTAssertFalse(store.state.fileExists)
    }

    func testInvalidEditKeepsLastValidThemeWithOneDiagnostic() throws {
        try write(valid)
        let store = ThemeStore(directory: folder)
        let good = store.state.document
        XCTAssertNotNil(good)
        try write(#"{"version": 1, "global": "missing"}"#)
        store.reload()
        XCTAssertEqual(store.state.document, good)
        XCTAssertEqual(store.state.diagnostic,
                       #"themes.json: global: scheme "missing" is not defined in schemes. Fix the file and choose Reload themes; keeping the last valid theme."#)
        try write(valid)
        store.reload()
        XCTAssertNil(store.state.diagnostic)
    }

    func testInvalidFileWithNoPriorValidUsesBuiltIn() throws {
        try write("not json")
        let store = ThemeStore(directory: folder)
        XCTAssertNil(store.state.document)
        XCTAssertTrue(store.state.diagnostic?.hasSuffix("using built-in colors.") ?? false)
    }

    func testReloadPicksUpEditsAndRestartReadsSameFile() throws {
        try write(valid)
        let store = ThemeStore(directory: folder)
        XCTAssertEqual(store.state.globalScheme, "slate")
        try write(valid.replacingOccurrences(of: #""global": "slate""#, with: #""global": "warm""#))
        XCTAssertEqual(store.state.globalScheme, "slate", "no change until reload")
        store.reload()
        XCTAssertEqual(store.state.globalScheme, "warm")
        let restarted = ThemeStore(directory: folder)
        XCTAssertEqual(restarted.state, store.state)
    }

    func testEnsureFileNeverOverwrites() throws {
        XCTAssertTrue(try ThemeLoader.ensureFile(file))
        try write(valid)
        XCTAssertFalse(try ThemeLoader.ensureFile(file))
        XCTAssertEqual(try String(contentsOf: file), valid)
    }

    func testLowContrastWarnsButKeepsColors() throws {
        let doc = try parse(##"{"version": 1, "global": "dim", "schemes": {"dim": {"light": {"text": "#EEEEEE"}}}}"##).get()
        let state = ThemeState(document: doc, fileExists: true)
        let defaults: [String: (light: UInt32, dark: UInt32)] = [
            "text": (0x202124, 0xF1F2F3), "background": (0xF7F7F8, 0x242527), "panel": (0xFFFFFF, 0x303134), "muted": (0x5F6268, 0xB1B4BC),
        ]
        let warnings = ThemeContrast.warnings(state, defaults: defaults)
        XCTAssertEqual(warnings.count, 1, "light only: the scheme has no dark, so dark keeps built-in text")
        XCTAssertTrue(warnings[0].contains("global scheme \"dim\" (light)"))
        XCTAssertEqual(state.roles(for: nil)["text"]?.light, 0xEEEEEE)
        XCTAssertTrue(ThemeContrast.warnings(ThemeState(), defaults: defaults).isEmpty)
    }

    /// The owner's brief example, verbatim, including `surface`.
    static let briefExample = """
    {
      "version": 1,
      "global": "slate",
      "providers": { "claude": "warm", "claude-az": "violet" },
      "schemes": {
        "slate": {
          "light": { "accent": "#1769AA", "surface": "#F3F5F7" },
          "dark": { "accent": "#80B7F7", "surface": "#25272B" }
        },
        "warm": {
          "light": { "accent": "#9A4A24" },
          "dark": { "accent": "#EAAF86" }
        },
        "violet": {
          "light": { "accent": "#7245B8" },
          "dark": { "accent": "#B39AEF" }
        }
      }
    }
    """

    func testBriefExampleLoadsWithSurfaceAlias() throws {
        let doc = try parse(Self.briefExample).get()
        XCTAssertEqual(doc.roles(for: nil)["panel"], ThemeColor(light: 0xF3F5F7, dark: 0x25272B))
        XCTAssertEqual(doc.roles(for: "claude-az")["accent"]?.dark, 0xB39AEF)
    }

    func testColorSyntaxAndTypes() {
        func fails(_ value: String) -> Bool {
            if case .failure = parse("{\"version\": 1, \"schemes\": {\"a\": {\"light\": {\"accent\": " + value + "}}}}") { return true }
            return false
        }
        XCTAssertTrue(fails("\"#ABC\""), "#RGB")
        XCTAssertTrue(fails("\"#AABBCCDD\""), "#RRGGBBAA")
        XCTAssertTrue(fails("\"AABBCC\""), "missing #")
        XCTAssertTrue(fails("123"))
        XCTAssertTrue(fails("null"))
        XCTAssertFalse(fails("\"#aabbcc\""))
        if case .success = parse(#"{"version": "1"}"#) { XCTFail("string version") }
        if case .success = parse(#"{"version": 1, "schemes": []}"#) { XCTFail("schemes array") }
        if case .success = parse(#"{"version": 1, "providers": {"x": 3}}"#) { XCTFail("provider value") }
        if case .success = parse(#"[1]"#) { XCTFail("array root") }
    }

    func testNonUTF8AndOversizeAndDirectory() throws {
        try Data([0xFF, 0xFE, 0x00, 0xC3, 0x28]).write(to: file)
        XCTAssertTrue(ThemeLoader.load(file).diagnostic?.contains("not valid JSON") ?? false)
        try Data(repeating: 0x20, count: ThemeParser.maxBytes + 10).write(to: file)
        XCTAssertTrue(ThemeLoader.load(file).diagnostic?.contains("the limit is 64 KB") ?? false)
        try FileManager.default.removeItem(at: file)
        try FileManager.default.createDirectory(at: file, withIntermediateDirectories: false)
        let state = ThemeLoader.load(file)
        XCTAssertTrue(state.diagnostic?.contains("is not a regular file") ?? false)
        XCTAssertNil(state.document)
    }

    func testFIFORejectedAndSymlinkFollowedOnlyToRegularFile() throws {
        XCTAssertEqual(mkfifo(file.path, 0o600), 0)
        XCTAssertTrue(ThemeLoader.load(file).diagnostic?.contains("is not a regular file") ?? false)
        try FileManager.default.removeItem(at: file)
        let real = folder.appendingPathComponent("real.json")
        try Data(valid.utf8).write(to: real)
        try FileManager.default.createSymbolicLink(at: file, withDestinationURL: real)
        XCTAssertEqual(ThemeLoader.load(file).globalScheme, "slate")
        try FileManager.default.removeItem(at: file)
        try FileManager.default.createSymbolicLink(at: file, withDestinationURL: folder)
        XCTAssertTrue(ThemeLoader.load(file).diagnostic?.contains("is not a regular file") ?? false)
    }

    func testRestartWithInvalidFileKeepsLastValidCopy() throws {
        try write(valid)
        let first = ThemeStore(directory: folder)
        let copy = folder.appendingPathComponent(ThemeStore.lastValidName)
        XCTAssertTrue(FileManager.default.fileExists(atPath: copy.path))
        let mode = try FileManager.default.attributesOfItem(atPath: copy.path)[.posixPermissions] as? NSNumber
        XCTAssertEqual(mode?.intValue, 0o600)
        try write("{\"version\": 1, \"global\": \"#zz\"}")
        let restarted = ThemeStore(directory: folder)
        XCTAssertEqual(restarted.state.document, first.state.document)
        XCTAssertTrue(restarted.state.diagnostic?.contains("keeping the last valid theme") ?? false)
        // The copy is not replaced by the invalid file.
        XCTAssertEqual(try String(contentsOf: copy), valid)
    }

    func testOversizedLastValidCopyIsIgnored() throws {
        let copy = folder.appendingPathComponent(ThemeStore.lastValidName)
        try Data(repeating: 0x20, count: ThemeParser.maxBytes + 1).write(to: copy)
        if case .success = ThemeLoader.readBounded(copy) { XCTFail("an oversized copy is refused before reading") }
        try write("{\"version\": 1, \"global\": \"#zz\"}")
        let store = ThemeStore(directory: folder)
        XCTAssertNil(store.state.document)
        XCTAssertTrue(store.state.diagnostic?.contains("using built-in colors") ?? false)
    }

    func testFIFOLastValidCopyDoesNotBlockLaunch() throws {
        let copy = folder.appendingPathComponent(ThemeStore.lastValidName)
        XCTAssertEqual(mkfifo(copy.path, 0o600), 0)
        let done = expectation(description: "ThemeStore.init returns")
        let directory = folder!
        DispatchQueue.global().async {
            _ = ThemeStore(directory: directory)
            done.fulfill()
        }
        let result = XCTWaiter().wait(for: [done], timeout: 5)
        if result != .completed {
            // Unblock the reader so the hung thread can finish, then fail.
            let fd = open(copy.path, O_WRONLY | O_NONBLOCK)
            if fd >= 0 { close(fd) }
            XCTFail("ThemeStore.init blocked on a FIFO last-valid copy")
        }
    }

    func testRestartWithNoFileAndNoCopyIsBuiltIn() {
        XCTAssertEqual(ThemeStore(directory: folder).state, ThemeState())
    }

    func testDeletingFileReturnsToBuiltIn() throws {
        try write(valid)
        let store = ThemeStore(directory: folder)
        try FileManager.default.removeItem(at: file)
        store.reload()
        XCTAssertEqual(store.state, ThemeState())
        XCTAssertEqual(ThemeStore(directory: folder).state, ThemeState(), "also after restart")
    }

    func testStatusAndBackgroundContrastWarnings() throws {
        let defaults: [String: (light: UInt32, dark: UInt32)] = [
            "text": (0x202124, 0xF1F2F3), "background": (0xF7F7F8, 0x242527), "panel": (0xFFFFFF, 0x303134),
            "muted": (0x5F6268, 0xB1B4BC), "success": (0x286F3D, 0x86C999), "warning": (0x7D520F, 0xE7BC70),
            "danger": (0xB3261E, 0xF2A49C), "focus": (0x2E6CBA, 0x8FBDF7), "accent": (0x2E6CBA, 0x78AFF4),
            "accentBackground": (0xE8F0FB, 0x26354A), "warningBackground": (0xFFF4DD, 0x3E3423),
            "dangerBackground": (0xFDECEA, 0x45292A),
        ]
        func warnings(_ light: String) throws -> [String] {
            let doc = try parse("{\"version\": 1, \"global\": \"s\", \"schemes\": {\"s\": {\"light\": {" + light + "}}}}").get()
            return ThemeContrast.warnings(ThemeState(document: doc), defaults: defaults)
        }
        XCTAssertTrue(try warnings("\"danger\": \"#FFDDDD\"").first?.contains("danger on panel") ?? false)
        XCTAssertTrue(try warnings("\"accent\": \"#F0F0F0\"").first?.contains("accent on background") ?? false)
        XCTAssertTrue(try warnings("\"dangerBackground\": \"#202020\"").first?.contains("text on dangerBackground") ?? false)
        XCTAssertTrue(try warnings("\"warning\": \"#B3261F\"").first?.contains("warning and danger look alike") ?? false)
        XCTAssertEqual(try warnings("\"accent\": \"#1769AA\""), [])
        XCTAssertEqual(ThemeContrast.warnings(ThemeState(), defaults: defaults), [])
    }

    func testRolesMatchSemanticTokens() {
        XCTAssertEqual(ThemeRoles.all.count, 21)
        XCTAssertTrue(ThemeRoles.all.contains("series4"))
    }
}
