import AIUsageCore
import AIUsageDesign
import AppKit
import SwiftUI

/// Popover: 420 pt wide, height ≤ min(780, screen − 96). Header, status row,
/// notice and footer stay pinned; only the card list (or detail body) scrolls.
struct PopoverRoot: View {
    @Environment(AppStore.self) private var store
    @Environment(Navigation.self) private var navigation
    @Environment(\.openWindow) private var openWindow
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var detailProvider: String?
    @State private var returnFocus: String?
    private let detailMetric: ProviderDetailView.Metric
    private let screenHeight: CGFloat?

    init(initialDetail: String? = nil, detailMetric: ProviderDetailView.Metric = .tokens, screenHeight: CGFloat? = nil) {
        _detailProvider = State(initialValue: initialDetail)
        self.detailMetric = detailMetric
        self.screenHeight = screenHeight
    }

    private var maxHeight: CGFloat {
        let screen = screenHeight ?? NSScreen.main?.visibleFrame.height ?? 900
        return min(T.Layout.popoverMaxHeight, screen - T.Layout.popoverScreenMargin)
    }

    var body: some View {
        ZStack {
            OverviewView(openDetail: open, openHistory: showHistory, returnFocus: $returnFocus, maxHeight: maxHeight)
                .opacity(detailProvider == nil ? 1 : 0)
                .allowsHitTesting(detailProvider == nil)
                .accessibilityHidden(detailProvider != nil)
            if let id = detailProvider, let provider = store.report?.provider(id) {
                ProviderDetailView(provider: provider, back: back, openHistory: { showHistory(id) }, maxHeight: maxHeight,
                                   initialMetric: detailMetric)
                    .transition(reduceMotion ? .identity : .move(edge: .trailing).combined(with: .opacity))
            }
        }
        .frame(width: T.Layout.popoverWidth)
        .background(T.Color.background.color)
        .animation(reduceMotion ? nil : .easeOut(duration: T.Duration.medium), value: detailProvider)
        .onAppear { store.popoverOpened() }
    }

    private func open(_ id: String) {
        detailProvider = id
    }

    private func back() {
        let id = detailProvider
        detailProvider = nil
        returnFocus = id
    }

    private func showHistory(_ providerId: String?) {
        navigation.historyRequest = HistoryRequest(providerId: providerId)
        openWindow(id: HistoryView.windowId)
        NSApp.activate(ignoringOtherApps: true)
    }
}

/// Measures a view's height into a binding (used to size the scroll region to content).
struct HeightReader: ViewModifier {
    @Binding var height: CGFloat
    func body(content: Content) -> some View {
        content.background(GeometryReader { proxy in
            Color.clear
                .onAppear { height = proxy.size.height }
                .onChange(of: proxy.size.height) { _, value in height = value }
        })
    }
}

extension View {
    func readHeight(_ height: Binding<CGFloat>) -> some View { modifier(HeightReader(height: height)) }
}
