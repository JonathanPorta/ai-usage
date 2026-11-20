import AIUsageCore
import SwiftUI

/// Compact quota cell for cards: label, % used/remaining, meter, own reset/stale caption.
/// Each window carries its own freshness; a stale cell never shows a limit warning.
public struct QuotaCell: View {
    private let window: QuotaWindow
    private let now: Date
    private let compact: Bool

    public init(window: QuotaWindow, now: Date, compact: Bool = true) {
        self.window = window
        self.now = now
        self.compact = compact
    }

    private var stale: Bool { window.status == .stale }

    private var tint: ThemedColor {
        if stale { return T.Color.muted.color }
        if window.limit != nil { return T.Color.meterWarning.color }
        return T.Color.meterFill.color
    }

    public var body: some View {
        let caption = compact ? Presentation.compactQuotaCaption(window, now: now) : Presentation.quotaCaption(window, now: now)
        let reset = Presentation.quotaReset(window, now: now)
        VStack(alignment: .leading, spacing: 4) {
            if compact {
                Text(window.label).font(.auCaption).foregroundStyle(T.Color.muted.color).lineLimit(1)
            }
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text(Presentation.quotaValue(window))
                    .font(compact ? .system(size: 20, weight: .medium).monospacedDigit() : .auFigure)
                    .foregroundStyle(stale ? T.Color.muted.color : T.Color.text.color)
                    .fixedSize()
                Text(Presentation.quotaBasis(window)).font(.auCaption).foregroundStyle(T.Color.muted.color)
                    .fixedSize()
                if compact, let limit = window.limit {
                    // Inline on cards so every cell in a grid row keeps the same height.
                    Spacer(minLength: 2)
                    Label(limit == .reached ? "Reached" : "Almost used", systemImage: Symbols.limit)
                        .font(.auCaption.weight(.semibold)).foregroundStyle(T.Color.warning.color)
                        .lineLimit(1).minimumScaleFactor(0.8)
                        .accessibilityLabel(limit == .reached ? "Limit reached" : "Almost used")
                }
                if !compact {
                    Spacer()
                    Text(window.label).font(.auCaption).foregroundStyle(T.Color.muted.color)
                }
            }
            Meter(fraction: Presentation.meterFraction(window), tint: tint)
            if !compact, let limit = window.limit {
                Label(limit == .reached ? "Limit reached" : "Almost used", systemImage: Symbols.limit)
                    .font(.auCaption.weight(.semibold))
                    .foregroundStyle(T.Color.warning.color)
            }
            if compact {
                // One line per card cell: the countdown (live or last known) or the caption.
                HStack(spacing: 4) {
                    if caption.warning {
                        Image(systemName: Symbols.stale).foregroundStyle(T.Color.warning.color)
                    }
                    if let reset {
                        ResetLabel(reset.countdown)
                            .foregroundStyle(caption.warning ? T.Color.warning.color : T.Color.muted.color)
                    } else {
                        // Up to two lines so a long caption ("Reset time unavailable ·
                        // measured …") stays readable; the full text is also on hover.
                        Text(caption.text).lineLimit(2).fixedSize(horizontal: false, vertical: true)
                            .foregroundStyle(caption.warning ? T.Color.warning.color : T.Color.muted.color)
                            .help(caption.text)
                    }
                }
                .font(.auCaption)
            } else {
                HStack(spacing: 4) {
                    if caption.warning {
                        Image(systemName: window.staleCause == .auth ? Symbols.signIn : window.staleCause == .failed ? Symbols.failure : Symbols.stale)
                            .foregroundStyle(window.staleCause == .failed || window.staleCause == .auth ? T.Color.danger.color : T.Color.warning.color)
                    }
                    if let reset, reset.replacesCaption {
                        ResetLabel(reset.countdown)
                            .foregroundStyle(caption.warning ? T.Color.warning.color : T.Color.muted.color)
                    } else {
                        Text(caption.text)
                            .foregroundStyle(caption.warning ? T.Color.warning.color : T.Color.muted.color)
                    }
                    Spacer()
                    Text(stale ? "Stale" : "Current · measured \(Format.moment(window.measuredAt, now: now))")
                        .foregroundStyle(T.Color.muted.color)
                }
                .font(.auCaption)
                .lineLimit(2)
                if let reset, !reset.replacesCaption {
                    ResetLabel(reset.countdown).font(.auCaption).foregroundStyle(T.Color.warning.color)
                }
                if window.derived {
                    Text("Derived from the reported % used.").font(.auCaption).foregroundStyle(T.Color.muted.color)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(window.label)
        .accessibilityValue(accessibilityValue(caption.text))
    }

    private func accessibilityValue(_ caption: String) -> String {
        var parts = ["\(Presentation.quotaValue(window)) \(Presentation.quotaBasis(window))"]
        if window.limit == .reached { parts.append("limit reached") }
        if window.limit == .low { parts.append("almost used") }
        parts.append(stale ? "stale" : "current")
        parts.append(caption)
        if let reset = Presentation.quotaReset(window, now: now) {
            if !reset.replacesCaption { parts.append(reset.countdown.text) }
            if let exact = reset.countdown.exact { parts.append("resets \(exact)") }
        }
        return parts.joined(separator: ", ")
    }
}

/// A reset countdown ("Resets in 2 days") with a subtle dotted underline. The exact local
/// weekday, date, time and zone shows on hover (tooltip) and in a popover on click or
/// keyboard activation (Space/Return when focused). Unknown resets render plain text.
public struct ResetLabel: View {
    private let countdown: ResetCountdown
    @State private var showingExact = false
    @Environment(\.themePalette) private var palette
    @FocusState private var focused: Bool
    @Environment(\.resetRevealHosted) private var revealHosted

    public init(_ countdown: ResetCountdown) { self.countdown = countdown }

    public var body: some View {
        if let exact = countdown.exact {
            Button { showingExact.toggle() } label: {
                Text(countdown.text)
                    .underline(true, pattern: .dot, color: T.Color.muted.color.opacity(0.7).color(palette))
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help(exact)
            .popover(isPresented: $showingExact, arrowEdge: .bottom) {
                Text(exact).font(.auCaption).foregroundStyle(T.Color.text.color)
                    .padding(T.Space.s2)
            }
            .focused($focused)
            // Keyboard focus reveals the exact time inline; nothing takes focus away.
            // Hidden while the click/Space popover shows the same text.
            .overlay(alignment: .topLeading) {
                if focused && !showingExact && !revealHosted { ResetReveal(text: exact).offset(y: 18) }
            }
            // Inside a reveal host the text draws in the host's overlay, above every
            // later sibling (a local zIndex only orders it within its own stack).
            .anchorPreference(key: ResetRevealPreference.self, value: .bounds) { anchor in
                focused && !showingExact && revealHosted ? [ResetRevealItem(text: exact, anchor: anchor)] : []
            }
            .zIndex(focused ? 1 : 0)
            .accessibilityLabel(countdown.text)
            .accessibilityValue(exact)
            .accessibilityHint("Shows the exact reset time")
        } else {
            Text(countdown.text)
        }
    }
}

/// The inline exact-reset-time chip shown while a `ResetLabel` has keyboard focus.
struct ResetReveal: View {
    let text: String
    var body: some View {
        Text(text).font(.auCaption).foregroundStyle(T.Color.text.color)
            .padding(.horizontal, 6).padding(.vertical, 3)
            .background(T.Color.panel.color, in: RoundedRectangle(cornerRadius: T.Radius.small))
            .overlay(RoundedRectangle(cornerRadius: T.Radius.small).strokeBorder(T.Color.line.color))
            .fixedSize()
            .allowsHitTesting(false)
            .accessibilityHidden(true)
    }
}

struct ResetRevealItem: Equatable {
    let text: String
    let anchor: Anchor<CGRect>
}

struct ResetRevealPreference: SwiftUI.PreferenceKey {
    static let defaultValue: [ResetRevealItem] = []
    static func reduce(value: inout [ResetRevealItem], nextValue: () -> [ResetRevealItem]) {
        value.append(contentsOf: nextValue())
    }
}

private struct ResetRevealHostedKey: EnvironmentKey { static let defaultValue = false }

extension EnvironmentValues {
    var resetRevealHosted: Bool {
        get { self[ResetRevealHostedKey.self] }
        set { self[ResetRevealHostedKey.self] = newValue }
    }
}

extension View {
    /// Draws a focused `ResetLabel`'s exact-time reveal in this view's overlay, so it
    /// sits above all content inside it. Apply once at a screen or window root.
    /// Draws nothing (and changes no layout) while no reset label has focus.
    public func resetRevealHost() -> some View {
        environment(\.resetRevealHosted, true)
            .overlayPreferenceValue(ResetRevealPreference.self, alignment: .topLeading) { items in
                GeometryReader { proxy in
                    ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                        let rect = proxy[item.anchor]
                        ResetReveal(text: item.text).offset(x: rect.minX, y: rect.minY + 18)
                    }
                }
            }
    }
}
