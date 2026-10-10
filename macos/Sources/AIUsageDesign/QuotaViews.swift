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

    private var tint: Color {
        if stale { return T.Color.muted.color }
        if window.limit != nil { return T.Color.meterWarning.color }
        return T.Color.meterFill.color
    }

    public var body: some View {
        let caption = Presentation.quotaCaption(window, now: now)
        VStack(alignment: .leading, spacing: 4) {
            if compact {
                Text(window.label).font(.auCaption).foregroundStyle(T.Color.muted.color)
            }
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text(Presentation.quotaValue(window))
                    .font(compact ? .system(size: 20, weight: .medium).monospacedDigit() : .auFigure)
                    .foregroundStyle(stale ? T.Color.muted.color : T.Color.text.color)
                Text(Presentation.quotaBasis(window)).font(.auCaption).foregroundStyle(T.Color.muted.color)
                if !compact {
                    Spacer()
                    Text(window.label).font(.auCaption).foregroundStyle(T.Color.muted.color)
                }
            }
            Meter(fraction: Presentation.meterFraction(window), tint: tint)
            if let limit = window.limit {
                Label(limit == .reached ? "Limit reached" : "Almost used", systemImage: Symbols.limit)
                    .font(.auCaption.weight(.semibold))
                    .foregroundStyle(T.Color.warning.color)
            }
            HStack(spacing: 4) {
                if caption.warning {
                    Image(systemName: window.staleCause == .auth ? Symbols.signIn : window.staleCause == .failed ? Symbols.failure : Symbols.stale)
                        .foregroundStyle(window.staleCause == .failed || window.staleCause == .auth ? T.Color.danger.color : T.Color.warning.color)
                }
                Text(caption.text)
                    .foregroundStyle(caption.warning ? T.Color.warning.color : T.Color.muted.color)
                if !compact {
                    Spacer()
                    Text(stale ? "Stale" : "Current · measured \(Format.moment(window.measuredAt, now: now))")
                        .foregroundStyle(T.Color.muted.color)
                }
            }
            .font(.auCaption)
            .lineLimit(2)
            if window.derived && !compact {
                Text("Derived from the reported % used.").font(.auCaption).foregroundStyle(T.Color.muted.color)
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
        return parts.joined(separator: ", ")
    }
}
