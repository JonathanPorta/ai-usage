import AIUsageCore
import SwiftUI

/// The collapsed "Chart key" below a graph: legend and technical explanation.
/// Shared by provider detail and History; never shown without a graph.
public struct ChartKey<Content: View>: View {
    @Binding private var isExpanded: Bool
    private let content: Content

    public init(isExpanded: Binding<Bool>, @ViewBuilder content: () -> Content) {
        _isExpanded = isExpanded
        self.content = content()
    }

    public var body: some View {
        DisclosureGroup(isExpanded: $isExpanded) {
            VStack(alignment: .leading, spacing: T.Space.s2) { content }
                .font(.auCaption).foregroundStyle(T.Color.muted.color)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.top, T.Space.s1)
        } label: {
            Text("Chart key").font(.auCaption).foregroundStyle(T.Color.muted.color)
                .accessibilityHint("Legend and how the chart is drawn")
        }
        .disclosureGroupStyle(.fullRow)
    }
}

/// The app's one disclosure look: the whole header row (chevron, label and the
/// space after it) toggles, not only the small chevron. The header is a button,
/// so VoiceOver reads the label with an expanded/collapsed value.
public struct FullRowDisclosureStyle: DisclosureGroupStyle {
    public init() {}

    public func makeBody(configuration: Configuration) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                withAnimation(.easeOut(duration: T.Duration.fast)) { configuration.isExpanded.toggle() }
            } label: {
                HStack(spacing: 5) {
                    Image(systemName: "chevron.right")
                        .font(.system(size: 9, weight: .bold))
                        .foregroundStyle(T.Color.muted.color)
                        .rotationEffect(.degrees(configuration.isExpanded ? 90 : 0))
                        .frame(width: 10)
                        .accessibilityHidden(true)
                    configuration.label
                    Spacer(minLength: 0)
                }
                .frame(maxWidth: .infinity, minHeight: 20, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityValue(configuration.isExpanded ? "Expanded" : "Collapsed")
            if configuration.isExpanded {
                configuration.content
            }
        }
    }
}

public extension DisclosureGroupStyle where Self == FullRowDisclosureStyle {
    static var fullRow: FullRowDisclosureStyle { FullRowDisclosureStyle() }
}

/// A compact one-line empty state for a row inside a section: faded symbol,
/// a readable sentence, an optional detail and an optional action.
public struct InlineEmptyRow: View {
    private let copy: EmptyCopy
    private let action: (() -> Void)?

    public init(_ copy: EmptyCopy, action: (() -> Void)? = nil) {
        self.copy = copy
        self.action = action
    }

    public var body: some View {
        HStack(alignment: .center, spacing: T.Space.s2) {
            Image(systemName: copy.symbol).font(.system(size: 15))
                .foregroundStyle(T.Color.muted.color.opacity(0.55))
                .frame(width: 20)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 1) {
                if let action, copy.action != nil {
                    Button(copy.title, action: action).buttonStyle(.link).font(.auBody)
                } else {
                    Text(copy.title).font(.auBody).foregroundStyle(T.Color.text.color)
                }
                if !copy.detail.isEmpty {
                    Text(copy.detail).font(.auCaption).foregroundStyle(T.Color.muted.color)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .combine)
    }
}

/// Quiet centered empty state for the shared copy: faded icon, one sentence, a short
/// detail, and the primary action with an optional secondary one beside it.
public struct CopyEmptyState: View {
    private let copy: EmptyCopy
    private let run: ((EmptyCopy.Action) -> Void)?

    public init(_ copy: EmptyCopy, action: ((EmptyCopy.Action) -> Void)?) {
        self.copy = copy
        self.run = action
    }

    public var body: some View {
        VStack(spacing: T.Space.s2) {
            Image(systemName: copy.symbol).font(.system(size: 28, weight: .regular))
                .foregroundStyle(T.Color.muted.color.opacity(0.55))
                .accessibilityHidden(true)
            Text(copy.title).font(.auBody.weight(.semibold)).foregroundStyle(T.Color.text.color)
                .multilineTextAlignment(.center)
            Text(copy.detail).font(.auCaption).foregroundStyle(T.Color.muted.color)
                .multilineTextAlignment(.center).fixedSize(horizontal: false, vertical: true)
            if let run, copy.action != nil || copy.secondary != nil {
                HStack(spacing: T.Space.s2) {
                    if let primary = copy.action {
                        Button(primary.title) { run(primary) }.controlSize(.regular)
                    }
                    if let secondary = copy.secondary {
                        Button(secondary.title) { run(secondary) }.controlSize(.regular)
                    }
                }
                .padding(.top, T.Space.s1)
            }
        }
        .frame(maxWidth: .infinity, minHeight: 160)
        .padding(.horizontal, T.Space.s4)
    }
}
