// main.cpp — the ONLY .cpp file in this project.
// It just #includes the headers (which contain everything) and runs the demo.

#include <iostream>    // std::cout
#include <vector>      // std::vector for the polymorphic fleet
#include <memory>      // std::unique_ptr for safe heap allocation

// These headers contain full implementations (inline functions inside the class body)
// so #including them here brings in everything we need
#include "AutonomousDrone.hpp"   // pulls in MissionDrone → Drone → Vehicle → exceptions

int main() {
    std::cout << "╔══════════════════════════════════╗\n"
              << "║   OOP Drone Simulation in C++    ║\n"
              << "╚══════════════════════════════════╝\n\n";

    // ─────────────────────────────────────────────────────────────
    // 1. CREATE ONE OBJECT OF EACH CLASS
    //
    // std::make_unique<T>(...) → allocates T on the heap safely.
    // When unique_ptr goes out of scope, it automatically calls delete.
    // This is RAII: Resource Acquisition Is Initialization.
    // ─────────────────────────────────────────────────────────────

    auto drone = std::make_unique<Drone>("Falcon-1", 80.0f, 150.0f, 15.0f);

    auto mdrone = std::make_unique<MissionDrone>(
        "Scout-2", 90.0f, "Patrol Alpha",
        std::vector<std::tuple<float,float,float>>{
            {10.f, 20.f, 30.f}, {40.f, 50.f, 60.f}, {70.f, 80.f, 90.f}
        }
    );

    auto adrone = std::make_unique<AutonomousDrone>(
        "Atlas-3", 100.0f, "Deep Recon",
        std::vector<std::tuple<float,float,float>>{
            {5.f, 10.f, 15.f}, {20.f, 25.f, 30.f},
            {35.f, 40.f, 45.f}, {50.f, 55.f, 60.f}
        },
        std::tuple<float,float,float>{0.f, 0.f, 0.f},  // home = origin
        200.0f
    );

    // ─────────────────────────────────────────────────────────────
    // 2. POLYMORPHISM
    //
    // All three objects are stored as Vehicle* pointers.
    // They're Drone/MissionDrone/AutonomousDrone at runtime, but they all
    // "are" Vehicles because of public inheritance.
    //
    // .get() extracts the raw pointer from unique_ptr;
    // unique_ptr still owns the memory.
    // ─────────────────────────────────────────────────────────────
    std::vector<Vehicle*> fleet = { drone.get(), mdrone.get(), adrone.get() };

    std::cout << "──── Polymorphic get_info() ────\n";
    for (Vehicle* v : fleet) {
        // v->get_info() is called on a Vehicle* but the VTABLE routes
        // it to the correct override in the actual runtime class.
        // This is dynamic dispatch — the compiler doesn't hard-code which
        // get_info() to call; it looks it up at runtime.
        std::cout << v->get_info() << "\n";
    }

    // ─────────────────────────────────────────────────────────────
    // 3. SHOW THAT PRIVATE MEMBERS CANNOT BE ACCESSED DIRECTLY
    // ─────────────────────────────────────────────────────────────
    std::cout << "──── Private Members ────\n";
    std::cout << "Battery (via getter): " << drone->get_battery() << "%\n";
    // ❌ Uncommenting this line causes a compile error:
    // drone->battery_level = 50.0f;
    // error: 'battery_level' is a private member of 'Vehicle'
    std::cout << "(battery_level is private — inaccessible outside Vehicle)\n\n";

    // ─────────────────────────────────────────────────────────────
    // 4. EXCEPTION HANDLING
    // ─────────────────────────────────────────────────────────────
    std::cout << "──── Exception Handling ────\n";

    // 4a. BatteryDepletedError — drain a drone that's already at 0
    try {
        Drone dead("DeadBird", 0.0f);
        dead.drain_battery(10.0f);         // throws BatteryDepletedError
    } catch (const BatteryDepletedError& e) {
        std::cout << "[BatteryDepletedError] " << e.what() << "\n";
    }

    // 4b. AltitudeError — request altitude above the ceiling
    try {
        Drone low("LowFlyer", 90.0f, 50.0f);
        low.take_off(200.0f);              // throws AltitudeError (200 > 50)
    } catch (const AltitudeError& e) {
        std::cout << "[AltitudeError] " << e.what() << "\n";
    }

    // 4c. InvalidStateError — charge while not in charging state
    try {
        Drone grounded("Grounded", 50.0f); // status = "idle" by default
        grounded.charge_battery(20.0f, 60); // throws — not in "charging"
    } catch (const InvalidStateError& e) {
        std::cout << "[InvalidStateError] " << e.what() << "\n";
    }

    // 4d. Catching with the COMMON BASE — DroneException catches all drone errors
    try {
        Drone t("Tester", 0.0f);
        t.drain_battery(5.0f);             // throws BatteryDepletedError
    } catch (const DroneException& e) {
        // DroneException is the base of all three specific errors —
        // this one catch can handle any drone-related error collectively
        std::cout << "[DroneException base catch] " << e.what() << "\n";
    }

    // ─────────────────────────────────────────────────────────────
    // 5. FULL AUTONOMOUS MISSION
    //    Take off → iterate waypoints → simulate high obstacle → summary
    // ─────────────────────────────────────────────────────────────
    std::cout << "\n──── Full Autonomous Mission ────\n";

    try {
        adrone->take_off(50.0f);
        std::cout << "Atlas-3 airborne at 50m\n";

        adrone->set_ai_mode("auto");

        // Visit every waypoint until mission_complete() returns true
        while (!adrone->mission_complete()) {
            // C++17 structured bindings: unpacks tuple<float,float,float> into x, y, z
            auto [x, y, z] = adrone->next_waypoint();
            std::cout << "  Waypoint (" << x << ", " << y << ", " << z << ")\n";
        }
        std::cout << "All waypoints done.\n";

    } catch (const BatteryDepletedError& e) {
        std::cout << "[Mission abort — battery] " << e.what() << "\n";
    } catch (const DroneException& e) {
        std::cout << "[Mission abort] " << e.what() << "\n";
    }

    // High-severity obstacle → emergency_stop() fires inside detect_obstacle()
    std::cout << "\nSimulating high-severity obstacle...\n";
    adrone->detect_obstacle({25.f, 30.f, 35.f}, "high");
    std::cout << "Status after: " << adrone->get_status() << "\n";

    // Print the mission summary and full flight log
    std::cout << "\n" << adrone->mission_summary();
    std::cout << "\n" << adrone->get_flight_log();

    // ─────────────────────────────────────────────────────────────
    // 6. BATTERY STATUS ACROSS THE FLEET
    // ─────────────────────────────────────────────────────────────
    std::cout << "──── Fleet Battery Status ────\n";
    for (Vehicle* v : fleet)
        std::cout << v->get_name() << ": " << v->get_battery() << "%"
                  << (v->is_critical() ? " [CRITICAL]" : "") << "\n";

    // unique_ptrs destroyed here → ~AutonomousDrone → ~MissionDrone → ~Drone → ~Vehicle
    // Virtual destructor in Vehicle ensures all destructors run in the right order
    std::cout << "\n[Done — all memory freed automatically]\n";
    return 0;
}
