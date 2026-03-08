import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

interface ActionItem {
  id?: string;
  owner_name: string;
  owner_email: string;
  title: string;
  description: string;
  due_date: string;
}

interface MeetingData {
  title: string;
  date: string;
  summary: string;
  decisions: string;
  risks: string;
  agreements: string;
  action_items: ActionItem[];
}

@Component({
  selector: 'app-curation-panel',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './curation-panel.component.html',
  styleUrl: './curation-panel.component.css'
})
export class CurationPanelComponent {
  // Mock Data Simulando la respuesta de Groq desde el Backend
  meetingData: MeetingData = {
    title: "Proyecto Alpha - Seguimiento Mensual",
    date: "2026-03-07",
    summary: "Se discutió el avance del desarrollo backend y la integración de la API de Groq. El equipo de frontend necesita más tiempo para la UI.",
    decisions: "1. Utilizar SQLModel en lugar de SQLAlchemy puro. 2. Construir la UI con Glassmorphism.",
    risks: "Posible retraso en la entrega de Firebase Auth. Dependencia crítica de la API de terceros.",
    agreements: "Todos acordaron tener un daily standup a las 10:00 AM.",
    action_items: [
      {
        id: "task-1",
        owner_name: "Ana Dev",
        owner_email: "ana@empresa.com",
        title: "Implementar Webhooks de Fireflies",
        description: "Crear el endpoint en FastAPI que reciba el JSON de Fireflies y lo encole.",
        due_date: "2026-03-10"
      },
      {
        id: "task-2",
        owner_name: "Carlos UI",
        owner_email: "carlos@empresa.com",
        title: "Crear Layout de Curación",
        description: "Maquetar en Angular el panel principal con CSS moderno.",
        due_date: "2026-03-09"
      }
    ]
  };

  isApproving = false;
  isApproved = false;

  removeTask(index: number) {
    this.meetingData.action_items.splice(index, 1);
  }

  addTask() {
    this.meetingData.action_items.push({
      id: "task-" + Date.now(),
      owner_name: "",
      owner_email: "",
      title: "Nueva Tarea",
      description: "",
      due_date: ""
    });
  }

  approveAct() {
    this.isApproving = true;
    
    // Simula petición POST al backend
    setTimeout(() => {
      this.isApproving = false;
      this.isApproved = true;
      console.log('Enviado al backend para generar Word e inyectar en Trello/ADO:', this.meetingData);
    }, 1500);
  }
}
