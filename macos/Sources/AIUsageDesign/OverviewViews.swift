import AIUsageCore
import SwiftUI

/// The statistics popover content behind a card's trophy button.
public struct ProviderStatisticsView: View {
    private let name: String
    private let stats: ProviderStatistics

    public init(name: String, stats: ProviderStatistics) {
        self.name = name
        self.stats = stats
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: T.Space.s2) {
            Text(name).font(.auBody.weight(.semibold)).foregroundStyle(T.Color.text.color)
            Grid(alignment: .leading, horizontalSpacing: T.Space.s3, verticalSpacing: T.Space.s1) {
                row("Today", stats.today.value, stats.today.note)
                row(stats.totalLabel, stats.recordedTotal,
                    stats.coverage + (stats.missingDays.map { "\n\($0)" } ?? ""))
                row("Peak (\(stats.rangeDays) days)", stats.peakValue ?? "—", stats.peakDay ?? "No complete day with usage")
                row("Last activity", stats.lastActivity, nil)
            }
            Divider().padding(.vertical, 2)
            Text(([stats.basis] + (stats.cacheNote.map { [$0] } ?? [])).joined(separator: " · "))
                .font(.auCaption).foregroundStyle(T.Color.muted.color)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(T.Space.s3)
        .frame(width: 300, alignment: .leading)
        .accessibilityElement(children: .combine)
    }

    private func row(_ label: String, _ value: String, _ detail: String?) -> some View {
        GridRow(alignment: .firstTextBaseline) {
            Text(label).font(.auCaption).foregroundStyle(T.Color.muted.color)
            VStack(alignment: .leading, spacing: 0) {
                Text(value).font(.auBody.weight(.medium).monospacedDigit()).foregroundStyle(T.Color.text.color)
                if let detail {
                    Text(detail).font(.auCaption).foregroundStyle(T.Color.muted.color)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }
}

/// Equal-size icon-only toolbar button: 36×28 hit target, hover fill, tooltip and label.
public struct ToolbarIconButton: View {
    private let symbol: String
    private let label: String
    private let busy: Bool
    private let action: () -> Void
    @State private var hovering = false

    public init(symbol: String, label: String, busy: Bool = false, action: @escaping () -> Void) {
        self.symbol = symbol
        self.label = label
        self.busy = busy
        self.action = action
    }

    public var body: some View {
        Button(action: action) {
            ZStack {
                if busy {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: symbol).font(.system(size: 15, weight: .medium))
                }
            }
            .frame(width: 36, height: 28)
            .foregroundStyle(T.Color.text.color)
            .background(hovering ? T.Color.hover.color : .clear, in: RoundedRectangle(cornerRadius: T.Radius.small))
            .contentShape(Rectangle())
        }
        .buttonStyle(.borderless)
        .onHover { hovering = $0 }
        .help(label)
        .accessibilityLabel(label)
        .accessibilityValue(busy ? "Checking" : "")
    }
}

/// Quiet centered empty state: faded icon, one sentence and an optional action.
public struct QuietEmptyState: View {
    private let symbol: String?
    private let title: String
    private let detail: String
    private let actionTitle: String?
    private let action: (() -> Void)?
    private let busy: Bool

    public init(symbol: String?, title: String, detail: String, busy: Bool = false,
                actionTitle: String? = nil, action: (() -> Void)? = nil) {
        self.symbol = symbol
        self.title = title
        self.detail = detail
        self.busy = busy
        self.actionTitle = actionTitle
        self.action = action
    }

    public var body: some View {
        VStack(spacing: T.Space.s2) {
            if busy {
                ProgressView().controlSize(.small)
            } else if let symbol {
                Image(systemName: symbol).font(.system(size: 28, weight: .regular))
                    .foregroundStyle(T.Color.muted.color.opacity(0.55))
                    .accessibilityHidden(true)
            }
            Text(title).font(.auBody.weight(.semibold)).foregroundStyle(T.Color.text.color)
            Text(detail).font(.auCaption).foregroundStyle(T.Color.muted.color)
                .multilineTextAlignment(.center).fixedSize(horizontal: false, vertical: true)
            if let actionTitle, let action {
                Button(actionTitle, action: action).controlSize(.regular).padding(.top, T.Space.s1)
            }
        }
        .frame(maxWidth: .infinity, minHeight: 160)
        .padding(.horizontal, T.Space.s4)
    }
}
